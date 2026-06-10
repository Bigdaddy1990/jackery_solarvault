import json  # noqa: INP001
import pathlib

s = (
    pathlib
    .Path(r"M:/0.1.0/.claude/ha_custom_config_and_best_practices.json")
    .open(encoding="utf-8")
    .read()
)  # noqa: E501, SIM115

# string-aware placeholder repair
out = []
i = 0
in_str = False
esc = False
n = len(s)
while i < n:
    c = s[i]
    if in_str:
        if esc:
            esc = False
        elif c == chr(92):
            esc = True
        elif c == '"':
            in_str = False
        out.append(c)
        i += 1
    elif c == '"':
        in_str = True
        out.append(c)
        i += 1
    elif s.startswith("{...}", i) or s.startswith("[...]", i):
        out.append('"..."')
        i += 5
    else:
        out.append(c)
        i += 1
s2 = "".join(out)


def extract(key):  # noqa: ANN001, ANN202, PLR0912
    """Find "key": <value> and return parsed balanced value (first occurrence)."""
    needle = f'"{key}":'
    start = s2.find(needle)
    results = []
    while start != -1:
        j = start + len(needle)
        while j < len(s2) and s2[j] in " \t\r\n":
            j += 1
        if s2[j] in "{[":
            openc = s2[j]
            closec = "}" if openc == "{" else "]"
            depth = 0
            k = j
            instr = False
            e = False
            while k < len(s2):
                ch = s2[k]
                if instr:
                    if e:
                        e = False
                    elif ch == chr(92):
                        e = True
                    elif ch == '"':
                        instr = False
                elif ch == '"':
                    instr = True
                elif ch == openc:
                    depth += 1
                elif ch == closec:
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            frag = s2[j : k + 1]
            try:
                results.append(json.loads(frag))
            except Exception as ex:  # noqa: BLE001
                results.append(f"PARSE_FAIL: {ex}")
        start = s2.find(needle, start + 1)
    return results


import sys  # noqa: E402

keys = sys.argv[1:]
for key in keys:
    vals = extract(key)
    for _v in vals[:2]:
        pass
