import csv, json
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

TRANSPORT_TO_KEY = {
    'car': 'driving-car', 'pedestrian': 'foot-walking',
    'bicycle': 'cycling-regular', 'public_transport': 'public_transport'
}
PENALTY_BY_PRIORITY = {1: 10_000_000, 2: 2_000_000, 3: 500_000}
FIXED_COST_PER_ENGINEER = 100_000

def parse_time(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m

def fmt_time(m):
    return f'{m // 60:02d}:{m % 60:02d}'

def load_data():
    with open('engineers_updated.csv', encoding='utf-8-sig') as f:
        engineers = list(csv.DictReader(f))
    with open('requests.csv', encoding='utf-8') as f:
        requests = list(csv.DictReader(f, delimiter=';'))
    with open('distance_matrix.json', encoding='utf-8') as f:
        matrix = json.load(f)
    return engineers, requests, matrix

def eng_skills(e):
    s = set()
    if e['skill_local'] == '1': s.add('local')
    if e['skill_connection'] == '1': s.add('connection')
    if e['skill_emergency'] == '1': s.add('emergency')
    return s

def build_model():
    engineers, requests, matrix = load_data()
    ids = matrix['ids']
    node_ids = ['OFFICE_EAST'] + [r['request_id'] for r in requests]
    matrix_idx = [ids.index(rid) for rid in node_ids]
    n_nodes = len(node_ids)
    n_vehicles = len(engineers)

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    time_cb_idx = []
    cost_cb_idx = []
    service_time = [0] + [int(r['service_time_min']) for r in requests]

    for e in engineers:
        key = TRANSPORT_TO_KEY[e['transport_type']]

        def make_time_cb(key=key):
            def cb(from_index, to_index):
                from_node = manager.IndexToNode(from_index)
                if manager.IndexToNode(to_index) == 0:
                    return service_time[from_node]
                fi = matrix_idx[from_node]
                ti = matrix_idx[manager.IndexToNode(to_index)]
                travel_sec = matrix[key][fi][ti]
                return int(travel_sec / 60) + service_time[from_node]

            return cb

        def make_cost_cb(key=key):
            def cb(from_index, to_index):
                if manager.IndexToNode(to_index) == 0:
                    return 0
                fi = matrix_idx[manager.IndexToNode(from_index)]
                ti = matrix_idx[manager.IndexToNode(to_index)]
                km = matrix[key + '_km'][fi][ti]
                return int(km * 100)
            return cb

        time_cb_idx.append(routing.RegisterTransitCallback(make_time_cb()))
        cost_cb_idx.append(routing.RegisterTransitCallback(make_cost_cb()))

    for v in range(n_vehicles):
        routing.SetArcCostEvaluatorOfVehicle(cost_cb_idx[v], v)
        routing.SetFixedCostOfVehicle(FIXED_COST_PER_ENGINEER, v)

    routing.AddDimensionWithVehicleTransits(time_cb_idx, 24 * 60, 24 * 60, False, 'Time')
    time_dim = routing.GetDimensionOrDie('Time')

    for v, e in enumerate(engineers):
        start, end = parse_time(e['shift_start']), parse_time(e['shift_end'])
        time_dim.CumulVar(routing.Start(v)).SetRange(start, start)
        time_dim.CumulVar(routing.End(v)).SetMax(end)

    for node in range(1, n_nodes):
        req = requests[node - 1]
        idx = manager.NodeToIndex(node)
        ws, we = parse_time(req['window_start']), parse_time(req['window_end'])
        time_dim.CumulVar(idx).SetRange(ws, we)

    for node in range(1, n_nodes):
        req = requests[node - 1]
        idx = manager.NodeToIndex(node)
        allowed = []
        for v, e in enumerate(engineers):
            if req['required_skill'] not in eng_skills(e):
                continue
            if req['required_transport'] and req['required_transport'] != e['transport_type']:
                continue
            allowed.append(v)

        if len(allowed) > 0:
            routing.VehicleVar(idx).SetValues(allowed + [-1])
        else:
            routing.ActiveVar(idx).SetValue(0)

        routing.AddDisjunction([idx], PENALTY_BY_PRIORITY[int(req['priority'])])

    return manager, routing, node_ids, requests, engineers, matrix, matrix_idx

def explain_unassigned(rid, requests_by_id, engineers, assigned_intervals):
    req = requests_by_id[rid]
    ws, we = req['window_start'], req['window_end']

    if req['required_transport']:
        has_transport_and_skill = any(
            req['skill'] in e['skills'] and e['transport_type'] == req['required_transport']
            for e in engineers.values()
        )
        if not has_transport_and_skill:
            return f"нет исполнителя с навыком «{req['skill_name']}» и транспортом «{req['required_transport']}» одновременно"

    duration = req['duration']
    eligible = [eid for eid, e in engineers.items() if req['skill'] in e['skills']]
    covering_shift = []
    for eid in eligible:
        shift_start = engineers[eid]['shift_start_min']
        shift_end = engineers[eid]['shift_end_min']
        earliest_start = max(ws, shift_start)
        latest_start = min(we, shift_end - duration)
        if earliest_start <= latest_start:
            covering_shift.append(eid)

    if not covering_shift:
        return (f"нет инженера с навыком «{req['skill_name']}», который успевает выполнить работу "
                f"({duration} мин) в окне {fmt_time(ws)}–{fmt_time(we)} с учётом своей смены")

    all_busy = True
    for eid in covering_shift:
        overlaps = [b for b in assigned_intervals.get(eid, []) if not (b[1] <= ws or b[0] >= we)]
        if not overlaps:
            all_busy = False
            break
    if all_busy:
        return f"все инженеры с навыком «{req['skill_name']}» заняты в это время"

    return "не удалось построить маршрут в рамках лимита времени поиска"

def _build_plan_from_solution(manager, routing, node_ids, requests, engineers, matrix, matrix_idx, solution):
    requests_by_id = {}
    for r in requests:
        requests_by_id[r['request_id']] = {
            'skill': r['required_skill'], 'skill_name': r['required_skill'],
            'window_start': parse_time(r['window_start']), 'window_end': parse_time(r['window_end']),
            'duration': int(r['service_time_min']), 'required_transport': r['required_transport'],
            'lat': float(r['latitude']), 'lon': float(r['longitude']),
        }

    engineers_by_id = {}
    for e in engineers:
        engineers_by_id[e['engineer_id']] = {
            'skills': eng_skills(e), 'transport_type': e['transport_type'],
            'shift_start_min': parse_time(e['shift_start']), 'shift_end_min': parse_time(e['shift_end']),
        }

    time_dim = routing.GetDimensionOrDie('Time')
    total_km = 0.0
    km_by_engineer = {}
    assigned_intervals = {}
    plan_assigned = []

    for v, e in enumerate(engineers):
        eid = e['engineer_id']
        key = TRANSPORT_TO_KEY[e['transport_type']]
        index = routing.Start(v)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            next_index = solution.Value(routing.NextVar(index))
            next_node = manager.IndexToNode(next_index)
            fi, ti = matrix_idx[node], matrix_idx[next_node]

            # км до депо (возврата) не считаем — его физически нет, ТЗ этого не требует
            km = matrix[key + '_km'][fi][ti] if next_node != 0 else 0.0
            total_km += km
            km_by_engineer[eid] = km_by_engineer.get(eid, 0) + km

            if node != 0:
                rid = node_ids[node]
                arrival = solution.Value(time_dim.CumulVar(index))
                dur = requests_by_id[rid]['duration']
                assigned_intervals.setdefault(eid, []).append((arrival, arrival + dur))
                transport_ru = {'car': 'автомобиль', 'pedestrian': 'пешком',
                                 'bicycle': 'велосипед', 'public_transport': 'общественный транспорт'}[e['transport_type']]
                window_end = requests_by_id[rid]['window_end']
                slack = window_end - arrival
                risk = slack < 15
                plan_assigned.append({
                    'request_id': rid, 'engineer_id': eid,
                    'arrival': fmt_time(arrival),
                    'duration_min': dur,
                    'transport': e['transport_type'],
                    'required_skill': requests_by_id[rid]['skill'],
                    'required_transport': requests_by_id[rid]['required_transport'],
                    'slack_min': slack,
                    'risk': risk,
                    'lat': requests_by_id[rid]['lat'], 'lon': requests_by_id[rid]['lon'],
                    'reason': f"навык и транспорт ({transport_ru}) совпали, прибытие {fmt_time(arrival)}, "
                              f"работа {dur} мин, маршрут +{km:.1f} км"
                              + (f" ⚠ запас до конца окна всего {slack} мин" if risk else "")
                })


            index = next_index

    unassigned_ids = [node_ids[n] for n in range(1, len(node_ids))
                       if solution.Value(routing.NextVar(manager.NodeToIndex(n))) == manager.NodeToIndex(n)]

    plan_unassigned = []
    for rid in unassigned_ids:
        reason = explain_unassigned(rid, requests_by_id, engineers_by_id, assigned_intervals)
        plan_unassigned.append({
            'request_id': rid, 'reason': reason,
            'lat': requests_by_id[rid]['lat'], 'lon': requests_by_id[rid]['lon'],
        })

    used_engineers = len(set(a['engineer_id'] for a in plan_assigned))

    with open('offices.csv', encoding='utf-8') as f:
        f.readline()
        office_row = next(csv.DictReader(f, delimiter=';'))

    engineers_by_eid_full = {e['engineer_id']: e for e in engineers}
    engineers_summary = [
        {'engineer_id': eid,
         'skills': sorted(eng_skills(engineers_by_eid_full[eid])),
         'transport_type': engineers_by_eid_full[eid]['transport_type'],
         'jobs': len([a for a in plan_assigned if a['engineer_id'] == eid]),
         'km': round(km_by_engineer.get(eid, 0), 1)}
        for eid in sorted(set(a['engineer_id'] for a in plan_assigned))
    ]

    return {
        'metrics': {'assigned': len(plan_assigned), 'total': len(requests),
                    'engineers_used': used_engineers, 'total_km': round(total_km, 1)},
        'depot': {'lat': float(office_row['latitude']), 'lon': float(office_row['longitude'])},
        'engineers_summary': engineers_summary,
        'assigned': plan_assigned,
        'unassigned': plan_unassigned,
    }

def run_solver():
    manager, routing, node_ids, requests, engineers, matrix, matrix_idx = build_model()

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(20)

    solution = routing.SolveWithParameters(params)
    if not solution:
        return {'error': 'Решение не найдено'}

    plan = _build_plan_from_solution(manager, routing, node_ids, requests, engineers, matrix, matrix_idx, solution)

    with open('plan.json', 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    return plan

STRATEGIES = {
    'A': routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
    'B': routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
    'C': routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES,
}

def run_solver_with_strategy(strategy_key):
    manager, routing, node_ids, requests, engineers, matrix, matrix_idx = build_model()

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = STRATEGIES[strategy_key]
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(15)

    solution = routing.SolveWithParameters(params)
    if not solution:
        return None

    return _build_plan_from_solution(manager, routing, node_ids, requests, engineers, matrix, matrix_idx, solution)

def run_solver_variants():
    variants = []
    for key in STRATEGIES:
        plan = run_solver_with_strategy(key)
        if plan:
            variants.append({'variant': key, **plan})
    return {'variants': variants}

if __name__ == '__main__':
    result = run_solver()
    print(f"Назначено: {result['metrics']['assigned']} из {result['metrics']['total']}")
    print(f"Бригад: {result['metrics']['engineers_used']}, пробег: {result['metrics']['total_km']} км")
