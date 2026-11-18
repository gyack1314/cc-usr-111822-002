from .evidence import at
from .dispatch import assigned
def available(db, known_at):
    charges=at(db,'charge',known_at);result=[]
    for job in assigned(db,known_at):
        if 'shift' not in job:result.append(job);continue
        used=sum(c['payload']['minutes'] for c in charges if c['payload']['shift']==job['shift'] and c['payload']['robot']==job['robot'])
        result.append({**job,'charge_minutes':used,'effective_minutes':max(0,job['minutes']-used)})
    return result
