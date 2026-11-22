"""Bounded streaming parser for rsync progress2; counters are logical bytes."""

import re

LINE = re.compile(
    r"^\s*([\d,]+)\s+(\d{1,3})%\s+(\d+(?:\.\d+)?[kKMGT]?B/s)\s+(\d+:\d{2}:\d{2})"
    r"(?:\s+\(xfr#(\d+),\s*(to-chk|ir-chk)=(\d+)/(\d+)\))?\s*$"
)


class Progress:
    def __init__(self):
        self.buffer = ""
        self.last = None

    def feed(self, text):
        lines = re.split(r"[\r\n]", self.buffer + text)
        self.buffer = lines.pop()[-4096:]
        for line in lines:
            match = LINE.fullmatch(line)
            if not match:
                continue
            size, percent, speed, clock, files, check, remaining, total = match.groups()
            if int(percent) > 100:
                continue
            self.last = {
                "bytes": int(size.replace(",", "")),
                "percent": int(percent),
                "speed": speed,
                "eta": clock if int(percent) < 100 else None,
                "files_transferred": int(files) if files else None,
                "files_remaining": int(remaining) if remaining else None,
                "files_total": int(total) if total else None,
                "listing_complete": check != "ir-chk" if check else False,
            }
        return self.last

    def finish(self):
        return self.feed("\n")
