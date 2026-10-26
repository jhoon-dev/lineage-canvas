# LineageCanvas

Generate portable, interactive table and job lineage explorers as single HTML files.

LineageCanvas reads table relationships through SQLAlchemy, builds upstream and downstream lineage, and embeds the result in a standalone HTML page. The generated page can be opened locally or published on any static host without running an application server.

## Features

- Works with MySQL, PostgreSQL, SQLite, SQL Server, Oracle, and other SQLAlchemy-supported databases
- Interactive upstream and downstream lineage graph
- Reference and trigger relationship filters
- Search, autocomplete, expand, collapse, pan, zoom, and minimap controls
- CSV export of graph and relationship data
- Standalone HTML output with no runtime database connection
- `.env`-based configuration
- Atomic output replacement so a failed build preserves the last valid page

## How it works

```text
Database -> Python generator -> standalone HTML -> static web server
```

The browser never connects to the database. Database access happens only while the Python generator is running.

## Choose a mode

```bash
python lineage_canvas.py --mode job    # Job Lineage: CSV job/table mappings
python lineage_canvas.py --mode table  # Table Lineage: database table relationships
```

The page heading and browser title identify **Job Lineage** or **Table Lineage**.
Omitting `--mode` keeps the existing Table Lineage behavior.

## Job Lineage (CSV)

Job Lineage uses the same Python → standalone HTML workflow, with a dedicated job graph.
No database settings or browser libraries are needed for this mode.

```bash
python lineage_canvas.py --mode job
```

By default this reads `job_table_mapping.csv` from the current directory and writes
`job_lineage_canvas.html`. The included sample contains a **5 Tables** connection,
duplicate mappings, a two-job cycle, and an in-place update.

```bash
python lineage_canvas.py --mode job --input job_table_mapping.csv --output job_lineage_canvas.html --initial-job J002
python lineage_canvas.py --mode job --input examples/legacy_job_lineage.csv --output legacy_jobs.html
```

Open the generated HTML directly in a modern browser. Each node previews up to
three READ and three WRITE tables; click the node for its complete lists and
upstream/downstream jobs. Click a connection's **5 Tables** label (or the line)
to view all shared tables. Nodes and labels also support Enter/Space, and the
table dialog closes with Escape. Search accepts job IDs, names, and table names.
Select a job to explore all upstream/downstream dependencies, or return to the
whole graph. Drag the canvas, use the wheel/zoom buttons, or fit it to the screen.
CSV export includes the currently visible jobs in the canonical mapping format.

### Default input: job_table_mapping.csv

```csv
job_id,job_name,table_name,relation_type
J001,Load orders,raw.orders,READ
J001,Load orders,stg.orders,WRITE
J002,Aggregate sales,stg.orders,READ
J002,Aggregate sales,mart.sales_daily,WRITE
```

- One row describes one Job/table READ or WRITE relationship.
- All four values are required. IDs distinguish jobs even when names match.
- READ/WRITE is case-insensitive; surrounding whitespace is removed. Job IDs
  and table names are case-sensitive. Use consistent fully qualified table names.
- Repeated `(job_id, table_name, relation_type)` rows count only once. A job ID
  with inconsistent names raises an error instead of silently merging jobs.
- Use UTF-8 (an Excel BOM is accepted). CSV quoting supports commas and newlines;
  IDs such as `001` and literal names such as `NA` are preserved.

### Legacy input

```csv
source_table,target_table,job_name
raw.orders,stg.orders,Load orders
stg.orders,mart.sales_daily,Aggregate sales
```

`source_table` becomes READ and `target_table` becomes WRITE. An optional `job_id`
is respected; otherwise `job_name` is the job identity. Repeated names therefore
merge into the same job in this format. Blank source or target supports jobs
that only read or only write; both cannot be blank. If both complete schemas
are present, the canonical four-column mapping takes precedence. The formats
are detected from headers, not filenames.

### Dependency and cycle rules

A → B exists when A writes at least one table that B reads. One directed edge
stores the sorted, unique intersection of those tables; its label counts tables,
not CSV rows. Multiple writers connect to every other job that reads the table.
The diagnostics dialog lists these ambiguous producers; table mappings alone do
not establish which execution produced the data.

READ and WRITE on the same job/table remain visible as an in-place operation;
they do not create a self-edge or imply a cycle across separate jobs. Cycles
between jobs are preserved, highlighted with amber dashed connections, and
listed by strongly connected group. Layout places each group at the same level,
then orders the condensed acyclic graph. Graph construction and traversal use
iterative algorithms so cycles and long chains terminate without recursion limits.
Disconnected jobs are retained. A header-only mapping produces an empty canvas.

### Python API and code structure

```python
from job_lineage import load_job_mapping_csv, export_job_lineage_canvas_html

data = export_job_lineage_canvas_html(
    load_job_mapping_csv("job_table_mapping.csv"),
    output_html="job_lineage_canvas.html",
    initial_job="J002",  # optional
)
```

- `lineage_canvas.py`: existing table graph API/database loader and shared safe,
  atomic HTML output; CLI routes `--mode job` to the job generator.
- `job_lineage.py`: CSV validation/normalization, job graph, cycle groups and layout.
- `job_lineage_canvas_template.html`: dependency-free job UI and table-list dialog.
- `lineage_canvas_template.html`: existing table explorer.
- `tests/test_job_lineage.py`: graph, CSV, CLI, safety, and table export regression tests.
- `tests/browser_job_lineage.js`: Playwright page function for the included sample;
  verifies dialogs, keyboard controls, graph filters, CSV export, pan/zoom, and mobile layout.

```bash
python -m unittest discover -s tests -v
```

## Requirements

- Python 3.10 or newer
- A SQLAlchemy-compatible relational database
- A database account with read-only access to the lineage table
- A Python driver for the selected database

## Installation

```bash
git clone https://github.com/jhoon-dev/lineage-canvas.git
cd lineage-canvas
python -m venv .venv
```

Activate the environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
source .venv/bin/activate
```

Install the core dependencies:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` includes PyMySQL for MySQL. Install the appropriate driver when using another database:

```bash
# PostgreSQL
python -m pip install "psycopg[binary]"

# SQL Server
python -m pip install pyodbc

# Oracle
python -m pip install oracledb
```

SQLite uses Python's built-in driver and needs no additional package.

## Lineage table format

By default, LineageCanvas reads a table named `table_lineage` with these columns:

| Column | Description | Example |
| --- | --- | --- |
| `source_table` | Upstream table name | `raw.orders` |
| `target_table` | Downstream table name | `mart.sales_daily` |
| `relation_type` | `reference` or `trigger` | `reference` |
| `edge_count` | Relationship weight | `1` |

Example definition—the exact numeric and text types may be adapted to your database:

```sql
CREATE TABLE table_lineage (
    source_table VARCHAR(255) NOT NULL,
    target_table VARCHAR(255) NOT NULL,
    relation_type VARCHAR(20) DEFAULT 'reference',
    edge_count DOUBLE PRECISION DEFAULT 1
);
```

The table may be schema-qualified, such as `metadata.table_lineage`. Table and column names are configurable.

## Configuration

Copy the example configuration:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS or Linux
cp .env.example .env
```

### Component-based connection

The included `.env.example` uses MySQL:

```dotenv
DB_DIALECT=mysql+pymysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=lineage_reader
DB_PASSWORD=change-me
DB_NAME=lineage_db
```

For PostgreSQL, for example:

```dotenv
DB_DIALECT=postgresql+psycopg
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=lineage_reader
DB_PASSWORD=change-me
DB_NAME=lineage_db
```

### SQLAlchemy URL

You can set `DATABASE_URL` instead of the component-based `DB_*` settings. This is also the recommended form for SQLite and advanced driver options such as TLS.

```dotenv
# PostgreSQL
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/lineage_db

# SQLite, relative to the project directory
DATABASE_URL=sqlite+pysqlite:///lineage.db

# SQL Server with an ODBC DSN
DATABASE_URL=mssql+pyodbc://user:password@my_dsn
```

If a URL contains reserved characters in a username or password, percent-encode them or use the component-based settings.

### Lineage and output settings

```dotenv
LINEAGE_TABLE=table_lineage
LINEAGE_SOURCE_COLUMN=source_table
LINEAGE_TARGET_COLUMN=target_table
LINEAGE_RELATION_COLUMN=relation_type
LINEAGE_METRIC_COLUMN=edge_count

OUTPUT_HTML=lineage_canvas.html
PAGE_TITLE=LineageCanvas
INITIAL_TABLE=
MAX_NEIGHBORS_PER_CLICK=80
UPSTREAM_AUTO_DEPTH=12
```

`INITIAL_TABLE` is optional. When omitted, LineageCanvas selects a suitable root table automatically.

Existing `MYSQL_*` variables from earlier versions remain supported for backward compatibility, but new configurations should use `DB_*` and `LINEAGE_*`.

## Generate the HTML

```bash
python lineage_canvas.py --mode table
```

The command prints the generated file path and number of lineage edges:

```text
Created /path/to/lineage_canvas.html (123 edges)
```

Open the file directly or serve the directory locally:

```bash
python -m http.server 8080
```

Then visit `http://localhost:8080/lineage_canvas.html`.

## Publish with GitHub Pages

GitHub Pages serves static files; it does not run the Python generator or connect to the database. Generate the page before publishing it.

```dotenv
OUTPUT_HTML=docs/index.html
```

Run the generator, review the result, and commit `docs/index.html`. In the repository settings, configure GitHub Pages to deploy from the `main` branch and `/docs` directory.

The generated HTML contains the queried lineage data. Publish it only when all embedded database, schema, and table names are safe to disclose. For private databases, generate the page locally instead of exposing the database to GitHub-hosted runners.

## Security

- Never commit `.env`; it is excluded by `.gitignore`.
- Use a dedicated database account with only the required `SELECT` permission.
- Do not expose a database port to the public internet solely for this tool.
- Treat the generated HTML as public data when publishing it on a public site.
- Rotate credentials immediately if they are ever committed to Git history.

## Development with example data

The `example_edges()` function provides a sample dataset for renderer development without a database:

```python
from lineage_canvas import example_edges, export_lineage_canvas_html

export_lineage_canvas_html(
    edge_df=example_edges(),
    output_html="lineage_canvas_example.html",
    title="LineageCanvas",
)
```

## License

LineageCanvas is available under the [MIT License](LICENSE).
