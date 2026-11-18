from .evidence import at
def assigned(db, known_at):
    shifts={r['id']:r for r in at(db,'shift',known_at)};result=[]
    for job in at(db,'job',known_at):
        p=job['payload'];shift=shifts.get(p['shift'])
        if not shift or p['robot'] not in shift['payload']['robots']:
            result.append({'job':job['id'],'status':'班次归属待核'});continue
        result.append({'job':job['id'],'shift':p['shift'],'robot':p['robot'],'station':p['station'],'task':p['task'],'minutes':p['minutes'],'units':p['units'],'source':job['digest']})
    return result
