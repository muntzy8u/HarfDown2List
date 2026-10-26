"""Audition a voice without touching the running app.

    venv/bin/python voice_preview.py                       # current voice
    venv/bin/python voice_preview.py --persona london_warm \
        --rate +12% --pitch -3Hz --volume +5%              # hear a tweak
    venv/bin/python voice_preview.py --list                # the voices
    venv/bin/python voice_preview.py --persona london_warm \
        --rate +12% --save                                 # keep it

Without --save this changes NOTHING: it is a separate process, it speaks
through your speakers, it writes no files, and the running app and the
next boot are both unaffected. With --save the tweaked rate/pitch/volume
are written back into the persona's line in voice_out.py, and they take
effect the next time the app starts.

NOTE: this talks to the speakers directly and knows nothing about the
running app, so do not run it while the app is reading something out -
you would get two voices at once.
"""
import argparse
import os
import re
import shutil
import sys

import config
import voice_out

VOICE_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "voice_out.py")
SAMPLE = ("Good evening. This is HarfDown2List speaking. "
          "This is how the voice sounds now.")


def list_personas():
    print("personas (the key is what you pass to --persona):")
    for key, label in voice_out.available_personas():
        p = voice_out.VOICES[key]
        print("  %-14s %-28s edge=%-20s rate=%-7s pitch=%-7s volume=%s"
              % (key, label, p.edge, p.rate, p.pitch, p.volume))
    print("\n  saved choice: %s" % voice_out.load_persona())


def save_knobs(key, rate=None, pitch=None, volume=None):
    """Write the tweaked knobs back into voice_out.py's VOICES entry."""
    with open(VOICE_OUT, encoding="utf-8") as f:
        src = f.read()
    pattern = re.compile(r'("%s"\s*:\s*_p\(.*?\))' % re.escape(key), re.S)
    m = pattern.search(src)
    if not m:
        print("could not find the %r entry in voice_out.py" % key)
        return False
    block = m.group(1)
    original = block
    for name, value in (("rate", rate), ("pitch", pitch), ("volume", volume)):
        if value is None:
            continue
        if re.search(r'\b%s="[^"]*"' % name, block):
            block = re.sub(r'\b%s="[^"]*"' % name, '%s="%s"' % (name, value),
                           block)
        else:
            # no such knob yet: add it just before the closing paren
            block = block[:-1].rstrip() + ', %s="%s")' % (name, value)
    if block == original:
        print("nothing to change")
        return False
    shutil.copyfile(VOICE_OUT, VOICE_OUT + ".bak")
    with open(VOICE_OUT, "w", encoding="utf-8") as f:
        f.write(src[:m.start(1)] + block + src[m.end(1):])
    print("saved to voice_out.py (backup at voice_out.py.bak):")
    print("  " + block.strip().replace("\n", " "))
    print("  -> takes effect next time the app starts")
    return True


# argparse reads a value that starts with "-" as another option, so
# "--pitch -4Hz" would fail with "expected one argument". Join those
# pairs into the "--pitch=-4Hz" form before parsing.
_VALUE_OPTS = ("--rate", "--pitch", "--volume", "--text", "--persona")


def _tidy_argv(argv):
    out = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if (arg in _VALUE_OPTS and i + 1 < len(argv)
                and argv[i + 1].startswith("-")
                and not argv[i + 1].startswith("--")):
            out.append("%s=%s" % (arg, argv[i + 1]))
            i += 2
            continue
        out.append(arg)
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--persona", help="persona key (default: the saved choice)")
    ap.add_argument("--rate", help="e.g. +9%%  (faster/slower)")
    ap.add_argument("--pitch", help="e.g. +2Hz")
    ap.add_argument("--volume", help="e.g. +0%%")
    # (--pitch -4Hz works too; the script tidies the argv for you)
    ap.add_argument("--text", help="what to say")
    ap.add_argument("--list", action="store_true", help="list the personas")
    ap.add_argument("--save", action="store_true",
                    help="write the tweaks back into voice_out.py")
    args = ap.parse_args(_tidy_argv(sys.argv[1:]))

    if args.list:
        list_personas()
        return 0

    key = args.persona or voice_out.load_persona()
    if key not in voice_out.VOICES:
        print("unknown persona %r (try --list)" % key)
        return 1
    p = voice_out.persona_for(key)
    if args.rate:
        p.rate = args.rate
    if args.pitch:
        p.pitch = args.pitch
    if args.volume:
        p.volume = args.volume

    print("%s | edge=%s rate=%s pitch=%s volume=%s"
          % (p.label, p.edge, p.rate, p.pitch, p.volume))
    if not args.save:
        print("(audition only - nothing is written; add --save to keep it)")
    voice_out.speak(args.text or SAMPLE, p)
    if args.save:
        save_knobs(key, args.rate, args.pitch, args.volume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
