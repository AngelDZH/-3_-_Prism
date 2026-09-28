import csv, json

def parse_time(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m

def load_engineers():
    engineers = []
    with open('engineers_updated.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            skills = set()
            if r['skill_local'] == '1': skills.add('local')
            if r['skill_connection'] == '1': skills.add('connection')
            if r['skill_emergency'] == '1': skills.add('emergency')
            engineers.append({
                'id': r['engineer_id'],
                'skills': skills,
                'transport': r['transport_type'],
                'shift_start': parse_time(r['shift_start']),
                'shift_end': parse_time(r['shift_end']),
                'current_time': parse_time(r['shift_start']),
                'current_point': 'OFFICE_EAST',
                'assignments': []
            })
    return engineers

def load_requests():
    with open('requests.csv', encoding='utf-8') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    for r in rows:
        r['window_start'] = parse_time(r['window_start'])
        r['window_end'] = parse_time(r['window_end'])
        r['service_time_min'] = int(r['service_time_min'])
        r['priority'] = int(r['priority'])
    #rows.sort(key=lambda r: (r['priority'], r['request_id']))
    return rows

def load_matrix():
    with open('distance_matrix.json', encoding='utf-8') as f:
        return json.load(f)

TRANSPORT_TO_KEY = {
    'car': 'driving-car', 'pedestrian': 'foot-walking',
    'bicycle': 'cycling-regular', 'public_transport': 'public_transport'
}

def travel(matrix, transport, from_id, to_id):
    key = TRANSPORT_TO_KEY[transport]
    i = matrix['ids'].index(from_id)
    j = matrix['ids'].index(to_id)
    seconds = matrix[key][i][j]
    km = matrix[key + '_km'][i][j]
    return seconds / 60, km  # минуты, км

def can_assign(engineer, request, matrix):
    if request['required_skill'] not in engineer['skills']:
        return None, 'нет навыка ' + request['required_skill']
    if request['required_transport'] and request['required_transport'] != engineer['transport']:
        return None, 'нет транспорта ' + request['required_transport']

    travel_min, km = travel(matrix, engineer['transport'], engineer['current_point'], request['request_id'])
    arrival = max(engineer['current_time'] + travel_min, request['window_start'])

    if arrival > request['window_end']:
        return None, 'не успевает в окно'
    if arrival + request['service_time_min'] > engineer['shift_end']:
        return None, 'не укладывается в смену'

    return (arrival, km), None

def run_baseline():
    engineers = load_engineers()
    requests = load_requests()
    matrix = load_matrix()

    unassigned = []
    assigned = []
    for req in requests:
        placed = False
        reason = 'не найден подходящий инженер'
        for eng in engineers:
            result, reason = can_assign(eng, req, matrix)
            if result:
                arrival, km = result
                arrival_int = int(round(arrival))
                eng['assignments'].append((req['request_id'], arrival_int, req['service_time_min']))
                eng['current_time'] = arrival_int + req['service_time_min']
                eng['current_point'] = req['request_id']
                eng['km'] = eng.get('km', 0) + km
                assigned.append({
                    'request_id': req['request_id'], 'engineer_id': eng['id'],
                    'arrival': f'{arrival_int // 60:02d}:{arrival_int % 60:02d}',
                })
                placed = True
                break
        if not placed:
            unassigned.append({'request_id': req['request_id'], 'reason': reason})

    used_engineers = [e for e in engineers if e['assignments']]
    total_km = sum(e.get('km', 0) for e in engineers)

    engineers_summary = [
        {'engineer_id': e['id'], 'jobs': len(e['assignments']), 'km': round(e.get('km', 0), 1)}
        for e in used_engineers
    ]

    return {
        'metrics': {
            'assigned': len(assigned), 'total': len(requests),
            'engineers_used': len(used_engineers), 'total_km': round(total_km, 1),
        },
        'engineers_summary': engineers_summary,
        'assigned': assigned,
        'unassigned': unassigned,
    }

if __name__ == '__main__':
    result = run_baseline()
    print(f"Назначено: {result['metrics']['assigned']} из {result['metrics']['total']}")
    print(f"Бригад: {result['metrics']['engineers_used']}, пробег: {result['metrics']['total_km']} км")

