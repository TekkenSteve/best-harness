# dsh runner (LAB)

`run_dsh_lab.py` runs one LAB task through `dsh --profile headless` on the host,
under a DSH_HOME staged per rollout by the harness2 adapter (`--dsh-home`; never
the user's `~/.dsh`). Same results layout and scoring path as the OpenCode runner:
`results/<run_id>/workspace/{documents,output}`, `metrics.json`, and the NDJSON
run-event stream in `dsh_session.jsonl` (parsed by `harness2.substrate.dsh.render_events`).

The nudge loop resumes the persisted session with `--session-id`, the equivalent
of the OpenCode runner's `--session` continue. Model credentials come from the
launching environment (`DEEPSEEK_API_KEY`, optionally `DEEPSEEK_BASE_URL`).
