"""Exact CPU safety predicate from the original ship-rename acceptance job.

Source: resume-20261014/ship-rename-actual/accept-ship-rename.py::safety,
original lines 116-129. Only the predicate is relocated. Real production-helper
plans/owners/receipts still come from the unchanged ship_rename example tests.
This fixture performs no native work and never turns unknown into success.
"""

def safety(record,plan):
    reasons=[]
    if plan is not None:
        owner=plan.get('owner')
        if plan.get('pending') is not None:reasons.append('exact originalquery pending/unknown')
        if plan.get('unadopted_creation_receipt') is not None:reasons.append('originalfactoryreceipt notadopted/unknownowner')
        if owner is not None and (owner.command or owner.pending is not None):reasons.append('standaloneuniqueowner/pending remains')
        if (plan.get('submit_attempt') or{}).get('handoff_uncertain'):reasons.append('nativehandoff delivery/effects unknown despitecommand0')
        if (plan.get('destroy_attempt') or{}).get('destroy_uncertain'):reasons.append('original nativeDestroy outcome uncertain')
    if record['live_allocations']:reasons.append('callerallocation ledger remains')
    if record['cleanup_errors']:reasons.append('known cleanup error retained')
    record['safe_to_discard']=not reasons
    record['safe_to_discard_reasons']=reasons
    return not reasons
