from .evidence import at
from .charging import available
def shift_report(db, known_at):
    takeovers=at(db,'takeover',known_at);result=[]
    for job in available(db,known_at):
        if 'shift' not in job:result.append(job);continue
        assists=[r for r in takeovers if r['payload']['job']==job['job']]
        result.append({'job':job['job'],'shift':job['shift'],'station':job['station'],'units':job['units'],'effective_minutes':job['effective_minutes'],'takeovers':len(assists),'staff_minutes':sum(a['payload']['minutes'] for a in assists)})
    return result
