import json

with open('distance_matrix.json', encoding='utf-8') as f:
    d = json.load(f)

d['public_transport_km'] = d['foot-walking_km']

with open('distance_matrix.json', 'w', encoding='utf-8') as f:
    json.dump(d, f, ensure_ascii=False)

print('Готово, public_transport_km добавлен')
