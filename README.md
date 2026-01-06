# ClickHouse Views to Mermaid

A tool that converts ClickHouse views into Mermaid diagram syntax for easy visualization of data pipeline dependencies.

## Features

- Parse ClickHouse view definitions parsing using ANTLR4 grammar from https://github.com/ClickHouse/ClickHouse/blob/master/utils/antlr
- Support for complex view hierarchies
- Generate Mermaid flowchart diagrams

## Output Sample
```mermaid
graph LR
  classDef chTable fill:#ffdd00,stroke:#000000,stroke-width:2px,color:#000000
  classDef chView fill:#d6e4f8,stroke:#154360,stroke-width:2px,color:#154360

  test.household:::chTable
  test.human:::chTable
  test.car:::chTable
  test.pet:::chTable
  test.v_pet_ownership:::chView
  test.v_human_profile:::chView
  test.v_car_inventory:::chView
  test.car -.-> test.v_car_inventory
  test.household -.-> test.v_car_inventory
  test.human -.-> test.v_car_inventory
  test.pet -.-> test.v_pet_ownership
  test.human -.-> test.v_human_profile
  test.v_car_inventory -.-> test.v_human_profile
```

## Installation

```bash
# Clone the repository
git clone https://github.com/dmserg/clickhouse-views-to-mermaid.git
cd clickhouse-views-to-mermaid
```

## Usage

Define env vars to run `ch_view_dependencies.py`
```bash
CH_HOST - host name
CH_PORT - port name
CH_USER - user name
CH_PASSWORD - password
CH_SECURE - 0/1 - secure connection
```

Then run
```
python ch_view_dependencies.py <output mmd file>
```

## License

MIT