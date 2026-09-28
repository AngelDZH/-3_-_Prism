from explain_unassigned import load_data, eng_skills, TRANSPORT_TO_KEY, parse_time, fmt_time

def _requests_index():
    engineers, requests, matrix = load_data()
    requests_by_id = {r['request_id']: r for r in requests}
    engineers_by_id = {e['engineer_id']: e for e in engineers}
    return requests_by_id, engineers_by_id, matrix

def _travel(matrix, transport_type, from_id, to_id):
    key = TRANSPORT_TO_KEY[transport_type]
    ids = matrix['ids']
    fi, ti = ids.index(from_id), ids.index(to_id)
    return matrix[key][fi][ti] / 60, matrix[key + '_km'][fi][ti]

def _compute_route(matrix, engineer, ordered_request_ids, requests_by_id):
    transport = engineer['transport_type']
    shift_start = parse_time(engineer['shift_start'])
    shift_end = parse_time(engineer['shift_end'])
    current_time = shift_start
    current_point = 'OFFICE_EAST'
    arrivals = {}
    total_km = 0.0
    for rid in ordered_request_ids:
        req = requests_by_id[rid]
        travel_min, km = _travel(matrix, transport, current_point, rid)
        arrival = max(current_time + travel_min, parse_time(req['window_start']))
        if arrival > parse_time(req['window_end']):
            return None, None
        finish = arrival + int(req['service_time_min'])
        if finish > shift_end:
            return None, None
        arrivals[rid] = int(round(arrival))
        total_km += km
        current_time = finish
        current_point = rid
    return arrivals, round(total_km, 1)

def run_reassign(plan, request_id, target_engineer_id):
    requests_by_id, engineers_by_id, matrix = _requests_index()

    if request_id not in requests_by_id:
        return {'error': f'Неизвестная заявка {request_id}'}
    if target_engineer_id not in engineers_by_id:
        return {'error': f'Неизвестный инженер {target_engineer_id}'}

    req = requests_by_id[request_id]
    target_eng = engineers_by_id[target_engineer_id]

    if req['required_skill'] not in eng_skills(target_eng):
        return {'error': f"У {target_engineer_id} нет навыка «{req['required_skill']}»"}
    if req['required_transport'] and req['required_transport'] != target_eng['transport_type']:
        return {'error': f"У {target_engineer_id} нет транспорта «{req['required_transport']}»"}

    assigned = plan['assigned']
    arrival_map = {a['request_id']: a['arrival'] for a in assigned}
    engineer_of = {a['request_id']: a['engineer_id'] for a in assigned}
    source_engineer_id = engineer_of.get(request_id)

    def route_of(eid, exclude=None):
        rids = [a['request_id'] for a in assigned if a['engineer_id'] == eid and a['request_id'] != exclude]
        return sorted(rids, key=lambda rid: arrival_map[rid])

    target_route_base = route_of(target_engineer_id, exclude=request_id)

    best = None
    for pos in range(len(target_route_base) + 1):
        candidate = target_route_base[:pos] + [request_id] + target_route_base[pos:]
        arrivals, km = _compute_route(matrix, target_eng, candidate, requests_by_id)
        if arrivals is not None and (best is None or km < best[1]):
            best = (candidate, km)

    if best is None:
        return {'error': f"{request_id} нельзя назначить {target_engineer_id}: "
                          f"не проходит по времени/смене ни в одной позиции маршрута"}

    new_target_route, _ = best

    routes_to_rebuild = {target_engineer_id: new_target_route}
    if source_engineer_id and source_engineer_id != target_engineer_id:
        routes_to_rebuild[source_engineer_id] = route_of(source_engineer_id, exclude=request_id)

    new_assigned = [a for a in assigned if a['engineer_id'] not in routes_to_rebuild]

    for eid, route in routes_to_rebuild.items():
        eng = engineers_by_id[eid]
        arrivals, _ = _compute_route(matrix, eng, route, requests_by_id)
        if arrivals is None:
            return {'error': f'Внутренняя ошибка пересчёта маршрута {eid}'}
        prev_point = 'OFFICE_EAST'
        for rid in route:
            r = requests_by_id[rid]
            _, km = _travel(matrix, eng['transport_type'], prev_point, rid)
            reason = (f"вручную переназначено диспетчером, прибытие {fmt_time(arrivals[rid])}, маршрут +{km:.1f} км"
                      if rid == request_id else
                      f"навык и транспорт совпали, прибытие {fmt_time(arrivals[rid])}, маршрут +{km:.1f} км")
            new_assigned.append({
                'request_id': rid, 'engineer_id': eid,
                'arrival': fmt_time(arrivals[rid]),
                'required_skill': r['required_skill'],
                'required_transport': r.get('required_transport', ''),
                'lat': float(r['latitude']), 'lon': float(r['longitude']),
                'reason': reason,
            })

            prev_point = rid

    new_unassigned = [u for u in plan['unassigned'] if u['request_id'] != request_id]

    engineers_summary = []
    total_km = 0.0
    for eid in sorted(set(a['engineer_id'] for a in new_assigned)):
        jobs = sorted([a for a in new_assigned if a['engineer_id'] == eid], key=lambda a: a['arrival'])
        route_ids = [j['request_id'] for j in jobs]
        _, km_total = _compute_route(matrix, engineers_by_id[eid], route_ids, requests_by_id)
        engineers_summary.append({
            'engineer_id': eid,
            'skills': sorted(eng_skills(engineers_by_id[eid])),
            'transport_type': engineers_by_id[eid]['transport_type'],
            'jobs': len(jobs), 'km': km_total
        })
        total_km += km_total

    return {
        'metrics': {
            'assigned': len(new_assigned), 'total': plan['metrics']['total'],
            'engineers_used': len(engineers_summary), 'total_km': round(total_km, 1),
        },
        'depot': plan['depot'],
        'engineers_summary': engineers_summary,
        'assigned': new_assigned,
        'unassigned': new_unassigned,
        'manual_change': f"{request_id} вручную переназначена на {target_engineer_id}",
    }

if __name__ == '__main__':
    from explain_unassigned import run_solver
    plan = run_solver()
    print(f"План построен: {plan['metrics']['assigned']} из {plan['metrics']['total']}")
    # быстрая проверка: попытка переноса первой заявки другому инженеру
    first = plan['assigned'][0]
    other_engineers = [e['engineer_id'] for e in plan['engineers_summary'] if e['engineer_id'] != first['engineer_id']]
    if other_engineers:
        result = run_reassign(plan, first['request_id'], other_engineers[0])
        print('Тест переназначения:', result.get('error', 'успех'))
