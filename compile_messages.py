"""Compile .po files to .mo binary format (pure Python, no gettext needed)."""
import os
import struct
import array

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')


def compile_po(po_path, mo_path):
    messages = []
    with open(po_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    msgid = msgstr = None
    in_msgid = in_msgstr = False

    for line in lines:
        line = line.strip()
        if line.startswith('msgid '):
            if msgid is not None and msgstr is not None:
                messages.append((msgid, msgstr))
            msgid = line[7:-1]  # strip msgid "..."
            in_msgid = True
            in_msgstr = False
        elif line.startswith('msgstr '):
            msgstr = line[8:-1]  # strip msgstr "..."
            in_msgstr = True
            in_msgid = False
        elif line.startswith('"') and line.endswith('"'):
            val = line[1:-1]
            if in_msgid:
                msgid += val
            elif in_msgstr:
                msgstr += val
        else:
            if msgid is not None and msgstr is not None:
                messages.append((msgid, msgstr))
            msgid = msgstr = None
            in_msgid = in_msgstr = False

    if msgid is not None and msgstr is not None:
        messages.append((msgid, msgstr))

    # Unescape backslash sequences in parsed strings
    def unescape(s):
        return s.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')

    # Include header (empty msgid) so gettext knows charset=UTF-8
    entries = [(unescape(k).encode('utf-8'), unescape(v).encode('utf-8')) for k, v in messages if v]
    entries.sort()

    # Build MO binary
    N = len(entries)
    offsets = []
    ids = b''
    strs = b''
    for k, v in entries:
        offsets.append((len(k), len(ids), len(v), len(strs)))
        ids += k + b'\x00'
        strs += v + b'\x00'

    keystart = 7 * 4 + N * 8 * 2
    valstart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for klen, koff, vlen, voff in offsets:
        koffsets += [klen, koff + keystart]
        voffsets += [vlen, voff + valstart]

    output = struct.pack('Iiiiiii', 0x950412de, 0, N, 7 * 4, 7 * 4 + N * 8, 0, 0)
    output += array.array('i', koffsets).tobytes()
    output += array.array('i', voffsets).tobytes()
    output += ids + strs

    with open(mo_path, 'wb') as f:
        f.write(output)


def main():
    count = 0
    for root, dirs, files in os.walk(LOCALE_DIR):
        for fn in files:
            if fn.endswith('.po'):
                po = os.path.join(root, fn)
                mo = po[:-3] + '.mo'
                compile_po(po, mo)
                count += 1
                print(f'Compiled: {os.path.relpath(mo, LOCALE_DIR)}')
    print(f'\nDone! Compiled {count} .mo files.')


if __name__ == '__main__':
    main()
