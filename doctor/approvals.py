# Approve or reject the doctor's approval-tier actions from the terminal
# (the dashboard has buttons for the same thing).
#
#   python -m doctor.approvals              # list pending approvals
#   python -m doctor.approvals approve 3
#   python -m doctor.approvals reject 3
import sys

from doctor.actions import decide, pending_approvals

USAGE = "usage: python -m doctor.approvals [approve <id> | reject <id>]"


def main(argv: list[str]) -> int:
    if not argv:
        rows = pending_approvals()
        if not rows:
            print("No pending approvals.")
        for r in rows:
            print(f"#{r['id']}  {r['action']}({r['target']})  incident #{r['incident_id']}  - {r['reason']}")
        return 0
    if len(argv) != 2 or argv[0] not in ("approve", "reject") or not argv[1].isdigit():
        print(USAGE)
        return 2
    cmd, approval_id = argv[0], int(argv[1])
    if approval_id not in {r["id"] for r in pending_approvals()}:
        print(f"#{approval_id} is not waiting for a decision.")
        return 1
    decide(approval_id, approve=(cmd == "approve"), by="human (terminal)")
    print(f"#{approval_id} {'approved' if cmd == 'approve' else 'rejected'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
