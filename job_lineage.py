"""Normalize job/table mappings and derive deterministic, cycle-aware job lineage."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import pandas as pd

from lineage_canvas import _render_canvas_html

MAPPING_COLUMNS = ["job_id", "job_name", "table_name", "relation_type"]
LEGACY_COLUMNS = {"source_table", "target_table", "job_name"}
TEMPLATE_PATH = Path(__file__).with_name("job_lineage_canvas_template.html")


def load_job_mapping_csv(path: str | Path = "job_table_mapping.csv") -> pd.DataFrame:
    # Preserve literal identifiers such as 001, NA and NULL; accept an Excel UTF-8 BOM.
    return normalize_job_mapping(pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig"))


def normalize_job_mapping(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate either input schema and deduplicate (job_id, table, READ/WRITE)."""
    df = frame.copy()
    df.columns = [str(col).strip().lstrip("\ufeff") for col in df.columns]
    if df.columns.duplicated().any():
        raise ValueError("중복 CSV 컬럼이 있습니다.")
    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).str.strip()

    if set(MAPPING_COLUMNS).issubset(df.columns):
        df = df[MAPPING_COLUMNS].copy()
        df["relation_type"] = df["relation_type"].str.upper()
        invalid = (df[MAPPING_COLUMNS] == "").any(axis=1) | ~df["relation_type"].isin(["READ", "WRITE"])
        if invalid.any():
            rows = [str(i + 2) for i, bad in enumerate(invalid) if bad]
            raise ValueError("필수 값 또는 READ/WRITE 오류 (CSV 레코드): " + ", ".join(rows[:20]))
    elif LEGACY_COLUMNS.issubset(df.columns):
        # Prefer supplied IDs; otherwise the legacy job_name is the stable identity.
        if "job_id" not in df.columns:
            df["job_id"] = df["job_name"]
        invalid = (df[["job_id", "job_name"]] == "").any(axis=1) | (
            (df["source_table"] == "") & (df["target_table"] == "")
        )
        if invalid.any():
            raise ValueError("기존 포맷에는 job_name과 source_table/target_table 중 하나 이상이 필요합니다.")
        reads = df[["job_id", "job_name", "source_table"]].rename(columns={"source_table": "table_name"})
        writes = df[["job_id", "job_name", "target_table"]].rename(columns={"target_table": "table_name"})
        df = pd.concat([reads.assign(relation_type="READ"), writes.assign(relation_type="WRITE")], ignore_index=True)
        df = df[df["table_name"] != ""]
    else:
        raise ValueError("CSV 컬럼은 job_id,job_name,table_name,relation_type 또는 source_table,target_table,job_name 이어야 합니다.")

    conflicts = df.groupby("job_id")["job_name"].nunique()
    if (conflicts > 1).any():
        raise ValueError("동일 job_id에 서로 다른 job_name이 있습니다: " + ", ".join(conflicts[conflicts > 1].index))
    return df[MAPPING_COLUMNS].drop_duplicates().sort_values(MAPPING_COLUMNS).reset_index(drop=True)


def _components(job_ids, children, parents):
    """Iterative Kosaraju: cycles and long chains never recurse on Python's stack."""
    visited, finish = set(), []
    for root in job_ids:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(children[root])))]
        while stack:
            node, neighbors = stack[-1]
            nxt = next(neighbors, None)
            if nxt is None:
                finish.append(node)
                stack.pop()
            elif nxt not in visited:
                visited.add(nxt)
                stack.append((nxt, iter(sorted(children[nxt]))))
    visited, groups = set(), []
    for root in reversed(finish):
        if root in visited:
            continue
        visited.add(root)
        group, stack = [], [root]
        while stack:
            node = stack.pop()
            group.append(node)
            for nxt in sorted(parents[node]):
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        groups.append(sorted(group))
    return sorted(groups)


def build_job_lineage(mapping: pd.DataFrame) -> dict:
    df = normalize_job_mapping(mapping)
    jobs, readers, writers = {}, defaultdict(set), defaultdict(set)
    for row in df.itertuples(index=False):
        job = jobs.setdefault(row.job_id, {"id": row.job_id, "name": row.job_name, "read_tables": [], "write_tables": []})
        key, index = ("read_tables", readers) if row.relation_type == "READ" else ("write_tables", writers)
        job[key].append(row.table_name)
        index[row.table_name].add(row.job_id)
    parents = {job: set() for job in jobs}
    children = {job: set() for job in jobs}
    shared = defaultdict(set)
    for table in sorted(writers.keys() & readers.keys()):
        for source in sorted(writers[table]):
            for target in sorted(readers[table]):
                # A job reading its own output is an in-place operation, not a
                # dependency on a separate job execution. Preserve it on the node.
                if source != target:
                    shared[source, target].add(table)
                    children[source].add(target)
                    parents[target].add(source)

    groups = _components(sorted(jobs), children, parents)
    component = {job: i for i, group in enumerate(groups) for job in group}
    successors = {i: set() for i in range(len(groups))}
    indegree = {i: 0 for i in successors}
    levels = {i: 0 for i in successors}
    for source, target in shared:
        a, b = component[source], component[target]
        if a != b and b not in successors[a]:
            successors[a].add(b)
            indegree[b] += 1
    queue = deque(i for i in successors if indegree[i] == 0)
    while queue:
        current = queue.popleft()
        for nxt in sorted(successors[current]):
            levels[nxt] = max(levels[nxt], levels[current] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    rows = defaultdict(int)
    for i, group in enumerate(groups):
        level = levels[i]
        for job_id in group:
            job = jobs[job_id]
            job.update(
                read_tables=sorted(job["read_tables"]), write_tables=sorted(job["write_tables"]),
                in_place_tables=sorted(set(job["read_tables"]) & set(job["write_tables"])),
                parents=sorted(parents[job_id]), children=sorted(children[job_id]),
                component=i, cyclic=len(group) > 1, level=level, row=rows[level],
            )
            rows[level] += 1
    edges = [
        {"source": source, "target": target, "tables": sorted(tables), "table_count": len(tables),
         "cyclic": component[source] == component[target]}
        for (source, target), tables in sorted(shared.items())
    ]
    return {
        "jobs": list(jobs.values()), "edges": edges,
        "cycles": [group for group in groups if len(group) > 1],
        "multi_writer_tables": [
            {"table": table, "job_ids": sorted(ids)} for table, ids in sorted(writers.items()) if len(ids) > 1
        ],
        "mapping_count": len(df),
    }


def export_job_lineage_canvas_html(
    mapping: pd.DataFrame, output_html: str = "job_lineage_canvas.html",
    initial_job: str | None = None, title: str = "LineageCanvas · Job Lineage",
    data_updated_at: str | None = None,
) -> dict:
    data = build_job_lineage(mapping)
    if initial_job is not None and initial_job not in {job["id"] for job in data["jobs"]}:
        raise ValueError(f"존재하지 않는 initial_job: {initial_job}")
    payload = {"data": data, "initial_job": initial_job, "title": title,
               "data_updated_at": data_updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _render_canvas_html(TEMPLATE_PATH, payload, title, output_html)
    return data
