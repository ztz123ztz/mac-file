# mac usage
Exit codes: 0 = pipeline done (see run-summary.json); 2 = setup/pipeline error.
Layout: `--workdir` holds prompt.txt, config.json, job.log, temp_* raw outputs,
run-summary.json. `--out` holds harvested stable names only.
Rules: one task per `--workdir` (temp_* collide); reproduce from config.json;
never delete failed runs; report token usage from the job.log tail.
First run needs network (clone + pip, ~1.5GB incl. OpenCASCADE binaries).
DASHSCOPE_API_KEY accepts any OpenAI-compatible key (SiliconFlow, OpenAI, DeepSeek...).
