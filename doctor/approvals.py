# Approve or reject the doctor's approval-tier actions from the terminal
# (the dashboard has buttons for the same thing).
#
#   python -m doctor.approvals              # list pending approvals
#   python -m doctor.approvals approve 3
#   python -m doctor.approvals reject 3
import sys

from doctor.actions import decide, pending_approvals


def main(argv: list[str]) -> None:
    if not argv:
        rows = pending_approvals()
        if not rows:
            print("No pending approvals.")
        for r in rows:
            print(f"#{r['id']}  {r['action']}({r['target']})  incident #{r['incident_id']}  - {r['reason']}")
        return
    cmd, approval_id = argv[0], int(argv[1])
    decide(approval_id, approve=(cmd == "approve"), by="human (terminal)")
    print(f"#{approval_id} {'approved' if cmd == 'approve' else 'rejected'}")


if __name__ == "__main__":
    main(sys.argv[1:])
