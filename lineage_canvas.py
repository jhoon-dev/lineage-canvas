
from __future__ import annotations

import json
import html
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


TEMPLATE_PATH = Path(__file__).resolve().with_name("lineage_canvas_template.html")
ENV_PATH = Path(__file__).resolve().with_name(".env")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 환경변수가 없습니다: {name}")
    return value


def _quote_identifier(identifier: str, engine) -> str:
    """Validate and quote a possibly schema-qualified SQL identifier."""
    parts = identifier.split(".")
    if not parts or any(not re.fullmatch(r"[A-Za-z0-9_$]+", part) for part in parts):
        raise ValueError(f"허용되지 않는 DB 식별자입니다: {identifier!r}")
    quote = engine.dialect.identifier_preparer.quote_identifier
    return ".".join(quote(part) for part in parts)


def _database_url() -> URL | str:
    """Build a SQLAlchemy URL from generic settings or legacy MySQL settings."""
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url

    dialect = os.getenv("DB_DIALECT", "").strip()
    if dialect:
        database = _required_env("DB_NAME")
        if dialect.startswith("sqlite"):
            return URL.create(drivername=dialect, database=database)

        port_text = os.getenv("DB_PORT", "").strip()
        try:
            port = int(port_text) if port_text else None
        except ValueError as exc:
            raise ValueError("DB_PORT는 숫자여야 합니다.") from exc

        return URL.create(
            drivername=dialect,
            username=_required_env("DB_USER"),
            password=_required_env("DB_PASSWORD"),
            host=_required_env("DB_HOST"),
            port=port,
            database=database,
        )

    # Backward compatibility for existing .env files created before the
    # database-neutral configuration was introduced.
    if os.getenv("MYSQL_HOST", "").strip():
        try:
            port = int(os.getenv("MYSQL_PORT", "3306"))
        except ValueError as exc:
            raise ValueError("MYSQL_PORT는 숫자여야 합니다.") from exc
        return URL.create(
            drivername="mysql+pymysql",
            username=_required_env("MYSQL_USER"),
            password=_required_env("MYSQL_PASSWORD"),
            host=_required_env("MYSQL_HOST"),
            port=port,
            database=_required_env("MYSQL_DATABASE"),
            query={"charset": "utf8mb4"},
        )

    raise RuntimeError(
        "DATABASE_URL 또는 DB_DIALECT/DB_NAME 등의 DB 환경변수를 설정해 주세요."
    )


def load_edges_from_database() -> pd.DataFrame:
    """Load lineage edges from any SQLAlchemy-supported database."""
    database_url = _database_url()
    engine_options = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }
    legacy_mysql_ssl_ca = os.getenv("MYSQL_SSL_CA", "").strip()
    if legacy_mysql_ssl_ca and make_url(database_url).get_backend_name() == "mysql":
        engine_options["connect_args"] = {"ssl": {"ca": legacy_mysql_ssl_ca}}

    engine = create_engine(
        database_url,
        **engine_options,
    )

    table = _quote_identifier(
        os.getenv("LINEAGE_TABLE") or os.getenv("MYSQL_LINEAGE_TABLE", "table_lineage"),
        engine,
    )
    source_col = _quote_identifier(
        os.getenv("LINEAGE_SOURCE_COLUMN") or os.getenv("MYSQL_SOURCE_COLUMN", "source_table"),
        engine,
    )
    target_col = _quote_identifier(
        os.getenv("LINEAGE_TARGET_COLUMN") or os.getenv("MYSQL_TARGET_COLUMN", "target_table"),
        engine,
    )
    relation_col = _quote_identifier(
        os.getenv("LINEAGE_RELATION_COLUMN") or os.getenv("MYSQL_RELATION_COLUMN", "relation_type"),
        engine,
    )
    metric_col = _quote_identifier(
        os.getenv("LINEAGE_METRIC_COLUMN") or os.getenv("MYSQL_METRIC_COLUMN", "edge_count"),
        engine,
    )

    query = text(f"""
        SELECT
            {source_col} AS source_table,
            {target_col} AS target_table,
            COALESCE({relation_col}, 'reference') AS relation_type,
            COALESCE({metric_col}, 1) AS edge_count
        FROM {table}
        WHERE {source_col} IS NOT NULL
          AND {target_col} IS NOT NULL
    """)

    try:
        with engine.connect() as connection:
            return pd.read_sql(query, connection)
    finally:
        engine.dispose()


def example_edges() -> pd.DataFrame:
    return pd.DataFrame({
        "source_table": [
            "raw.customer", "raw.order", "raw.product",
            "stg.customer", "stg.order", "stg.product",
            "mart.customer_seg", "mart.sales_daily",
            "mart.customer_seg", "mart.customer_seg_snapshot",
            "mart.customer_seg", "rpt.sales_dashboard",
            "ods.customer", "ods.order", "dw.dim_customer", "dw.fact_order",
        ],
        "target_table": [
            "stg.customer", "stg.order", "stg.product",
            "mart.customer_seg", "mart.customer_seg", "mart.sales_daily",
            "rpt.customer_dashboard", "rpt.sales_dashboard",
            "rpt.sales_dashboard", "mart.customer_seg",
            "mart.customer_seg_snapshot", "rpt.dashboard_view",
            "raw.customer", "raw.order", "mart.customer_seg", "mart.sales_daily",
        ],
        "edge_count": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    })


def clean_edges(
    edge_df: pd.DataFrame,
    source_col: str = "source_table",
    target_col: str = "target_table",
    metric_col: Optional[str] = "edge_count",
    relation_type: Optional[str] = None,
    relation_col: str = "relation_type",
) -> pd.DataFrame:
    df = edge_df.copy()
    df = df.dropna(subset=[source_col, target_col])
    df[source_col] = df[source_col].astype(str).str.strip()
    df[target_col] = df[target_col].astype(str).str.strip()

    df = df[
        (df[source_col] != "")
        & (df[target_col] != "")
        & (df[source_col] != df[target_col])
    ]

    if relation_type is not None:
        df[relation_col] = relation_type
    elif relation_col not in df.columns:
        df[relation_col] = "reference"

    df[relation_col] = (
        df[relation_col]
        .fillna("reference")
        .astype(str)
        .str.strip()
        .str.lower()
        .replace({
            "ref": "reference",
            "참조": "reference",
            "triggered": "trigger",
            "트리거": "trigger",
        })
    )
    df.loc[~df[relation_col].isin(["reference", "trigger"]), relation_col] = "reference"

    if metric_col and metric_col in df.columns:
        df[metric_col] = pd.to_numeric(df[metric_col], errors="coerce").fillna(1)
        out = df.groupby([source_col, target_col, relation_col], as_index=False)[metric_col].sum()
    else:
        out = (
            df.groupby([source_col, target_col, relation_col], as_index=False)
            .size()
            .rename(columns={"size": "edge_count"})
        )
        metric_col = "edge_count"

    out = out.rename(columns={
        source_col: "source_table",
        target_col: "target_table",
        metric_col: "edge_count",
        relation_col: "relation_type",
    })
    out["edge_count"] = pd.to_numeric(out["edge_count"], errors="coerce").fillna(1).astype(float)
    return out[["source_table", "target_table", "relation_type", "edge_count"]]


def build_adjacency(edge_df: pd.DataFrame):
    relation_types = ["reference", "trigger"]
    all_tables = set(edge_df["source_table"]).union(set(edge_df["target_table"]))

    parents = {r: {table: [] for table in all_tables} for r in relation_types}
    children = {r: {table: [] for table in all_tables} for r in relation_types}

    for row in edge_df.itertuples(index=False):
        src = row.source_table
        tgt = row.target_table
        relation = row.relation_type
        w = float(row.edge_count)
        children[relation].setdefault(src, []).append({"table": tgt, "weight": w, "relation_type": relation})
        parents[relation].setdefault(tgt, []).append({"table": src, "weight": w, "relation_type": relation})

    for relation in relation_types:
        for table in all_tables:
            parents[relation][table] = sorted(parents[relation].get(table, []), key=lambda x: (-x["weight"], x["table"]))
            children[relation][table] = sorted(children[relation].get(table, []), key=lambda x: (-x["weight"], x["table"]))

    table_stats = {}
    for table in all_tables:
        ref_p = parents["reference"].get(table, [])
        ref_c = children["reference"].get(table, [])
        trg_p = parents["trigger"].get(table, [])
        trg_c = children["trigger"].get(table, [])
        table_stats[table] = {
            "parent_count": len(ref_p) + len(trg_p),
            "child_count": len(ref_c) + len(trg_c),
            "reference_parent_count": len(ref_p),
            "reference_child_count": len(ref_c),
            "trigger_parent_count": len(trg_p),
            "trigger_child_count": len(trg_c),
            "in_weight": sum(x["weight"] for x in ref_p + trg_p),
            "out_weight": sum(x["weight"] for x in ref_c + trg_c),
        }

    return {
        "tables": sorted(all_tables),
        "parents": parents,
        "children": children,
        "table_stats": table_stats,
        "relation_types": relation_types,
    }

def export_lineage_canvas_html(
    edge_df: Optional[pd.DataFrame] = None,
    output_html: str = "lineage_canvas.html",
    reference_edges: Optional[pd.DataFrame] = None,
    trigger_edges: Optional[pd.DataFrame] = None,
    initial_table: Optional[str] = None,
    source_col: str = "source_table",
    target_col: str = "target_table",
    metric_col: Optional[str] = "edge_count",
    relation_col: str = "relation_type",
    max_neighbors_per_click: int = 80,
    upstream_auto_depth: int = 12,
    title: str = "LineageCanvas",
    data_updated_at: Optional[str] = None,
):
    frames = []
    if edge_df is not None:
        frames.append(clean_edges(edge_df, source_col, target_col, metric_col, relation_col=relation_col))
    if reference_edges is not None:
        frames.append(clean_edges(reference_edges, source_col, target_col, metric_col, relation_type="reference", relation_col=relation_col))
    if trigger_edges is not None:
        frames.append(clean_edges(trigger_edges, source_col, target_col, metric_col, relation_type="trigger", relation_col=relation_col))

    if not frames:
        raise ValueError("edge_df 또는 reference_edges/trigger_edges 중 하나 이상을 전달해야 합니다.")

    clean_df = pd.concat(frames, ignore_index=True)
    clean_df = clean_df.groupby(
        ["source_table", "target_table", "relation_type"], as_index=False
    )["edge_count"].sum()
    data = build_adjacency(clean_df)

    if initial_table is None:
        candidates = [
            t for t in data["tables"]
            if data["table_stats"][t]["parent_count"] > 0
            and data["table_stats"][t]["child_count"] == 0
        ]
        if candidates:
            initial_table = sorted(candidates, key=lambda t: (-data["table_stats"][t]["parent_count"], t))[0]
        elif data["tables"]:
            initial_table = data["tables"][0]
        else:
            initial_table = ""

    if data_updated_at is None:
        data_updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "data": data,
        "initial_table": initial_table,
        "max_neighbors_per_click": max_neighbors_per_click,
        "upstream_auto_depth": upstream_auto_depth,
        "title": title,
        "data_updated_at": data_updated_at,
    }

    _render_canvas_html(TEMPLATE_PATH, payload, title, output_html)
    return clean_df


def _render_canvas_html(template_path: Path, payload: dict, title: str, output_html: str):
    """Shared safe embedding and atomic output for table and job canvases."""
    # The JSON is embedded in a script element. Escape HTML-significant
    # characters so a value such as </script> cannot terminate that element.
    payload_json = (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    safe_title = html.escape(title)

    try:
        html_template = template_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"HTML 템플릿 파일을 찾을 수 없습니다: {template_path}"
        ) from exc

    html_text = html_template.replace("__TITLE__", safe_title).replace("__PAYLOAD__", payload_json)

    # Write completely before replacing the public file. If generation fails,
    # the previously generated HTML remains intact.
    output_path = Path(output_html).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temp_path.write_text(html_text, encoding="utf-8")
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)


# Backward-compatible alias for code written before the project rename.
export_lineage_explorer_html = export_lineage_canvas_html


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate a table or job LineageCanvas HTML file.")
    parser.add_argument("--mode", choices=["table", "job"], default="table")
    parser.add_argument("--input", help="Job CSV (default: job_table_mapping.csv)")
    parser.add_argument("--output", help="Generated HTML path")
    parser.add_argument("--initial-job", help="Initially selected job_id")
    args = parser.parse_args()
    load_dotenv(ENV_PATH)
    if args.mode == "job":
        from job_lineage import export_job_lineage_canvas_html, load_job_mapping_csv

        output_html = args.output or "job_lineage_canvas.html"
        data = export_job_lineage_canvas_html(
            load_job_mapping_csv(args.input or "job_table_mapping.csv"),
            output_html=output_html,
            initial_job=args.initial_job,
        )
        print(f"Created {Path(output_html).resolve()} ({len(data['jobs'])} jobs, {len(data['edges'])} edges)")
        return
    if args.input or args.initial_job:
        parser.error("--input and --initial-job require --mode job")
    database_edges = load_edges_from_database()
    output_html = args.output or os.getenv("OUTPUT_HTML", "lineage_canvas.html")
    cleaned = export_lineage_canvas_html(
        edge_df=database_edges,
        output_html=output_html,
        initial_table=os.getenv("INITIAL_TABLE") or None,
        max_neighbors_per_click=int(os.getenv("MAX_NEIGHBORS_PER_CLICK", "80")),
        upstream_auto_depth=int(os.getenv("UPSTREAM_AUTO_DEPTH", "12")),
        title=os.getenv("PAGE_TITLE", "LineageCanvas"),
    )
    print(f"Created {Path(output_html).resolve()} ({len(cleaned)} edges)")


if __name__ == "__main__":
    main()
