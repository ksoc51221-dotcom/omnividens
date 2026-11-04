"""python -m reelay.cloud prepare '<JOB JSON>' <workdir>   |   python -m reelay.cloud report <workdir>"""

import sys

USAGE = "usage: python -m reelay.cloud prepare '<JOB JSON>' <workdir> | report <workdir>"


def main(argv: list[str]) -> int:
    from reelay.cloud import prepare, report
    if len(argv) == 3 and argv[0] == "prepare":
        return prepare.run(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "report":
        return report.run(argv[1])
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
