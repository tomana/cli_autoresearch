# example_cpp

Minimal C++ target for the cli_autoresearch loop.

```
example_cpp/
├── CMakeLists.txt   # cmake_minimum_required 3.16, std=20, one exe
├── main.cpp         # the target the agent edits
├── program.md       # per-iteration brief for the agent
└── README.md        # this file
```

## Run

From the repo root:

```bash
uv run run.py example_cpp/program.md --cwd example_cpp --minutes 10
```

`--cwd example_cpp` is what makes the agent edit / build files in
this dir instead of the repo root.
