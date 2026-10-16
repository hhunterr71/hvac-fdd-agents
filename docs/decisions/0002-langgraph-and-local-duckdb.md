# LangGraph for orchestration, local DuckDB for data; Databricks deferred

Agents are orchestrated with LangGraph, which gives handoffs, human-approval interrupts, and checkpointing. Data lives in local DuckDB over Parquet instead of Databricks, so anyone can clone and run the project with no account or compute limits. The bronze/silver/gold layout and the Equipment Registry and Point Role tables follow Databricks conventions, so moving the data there later is a straight copy, not a redesign.

Ground truth (fault labels, Symptom Windows, Remediation Rubric) lives in a separate database file. Agent tools are never given a connection to it. This keeps the agents isolated from the answers by how the system is built, not by prompt instructions.
