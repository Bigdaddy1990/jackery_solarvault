import pathlib  # noqa: INP001

s = (
    pathlib
    .Path(r"M:/0.1.0/.claude/ha_custom_config_and_best_practices.json")
    .open(encoding="utf-8")
    .read()
)  # noqa: E501, SIM115
depth = 0
i = 0
in_str = False
esc = False
buf = ""
results = []
while i < len(s):
    c = s[i]
    if in_str:
        if esc:
            esc = False
        elif c == chr(92):
            esc = True
        elif c == '"':
            in_str = False
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] == ":" and depth <= 2:  # noqa: PLR2004
                results.append((depth, buf))
        else:
            buf += c
    elif c == '"':
        in_str = True
        buf = ""
    elif c in "{[":
        depth += 1
    elif c in "}]":
        depth -= 1
    i += 1

for _d, _k in results:
    pass
