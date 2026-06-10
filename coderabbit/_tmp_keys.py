import sys

s = open(r'M:/0.1.0/.claude/ha_custom_config_and_best_practices.json', encoding='utf-8').read()
depth = 0
i = 0
in_str = False
esc = False
buf = ''
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
            while j < len(s) and s[j] in ' \t\r\n':
                j += 1
            if j < len(s) and s[j] == ':' and depth <= 2:
                results.append((depth, buf))
        else:
            buf += c
    else:
        if c == '"':
            in_str = True
            buf = ''
        elif c in '{[':
            depth += 1
        elif c in '}]':
            depth -= 1
    i += 1

for d, k in results:
    if d == 1:
        print(k)
    elif d == 2:
        print('   .' + k)
