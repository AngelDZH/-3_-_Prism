import csv, json, os
import osmnx as ox
import networkx as nx
import sklearn as skl

SPEED_KMH = {'drive': 25, 'walk': 5, 'bike': 15}
ACCESS_BUFFER_SEC = 5 * 60
GRAPH_CACHE = {'drive': 'graph_drive.graphml', 'walk': 'graph_walk.graphml', 'bike': 'graph_bike.graphml'}

def load_points():
    points = []
    with open('offices.csv', encoding='utf-8') as f:
        f.readline()
        for r in csv.DictReader(f, delimiter=';'):
            points.append((r['office_id'], float(r['latitude']), float(r['longitude'])))
    with open('requests.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter=';'):
            points.append((r['request_id'], float(r['latitude']), float(r['longitude'])))
    return points

def get_graph(network_type, north, south, east, west):
    cache_file = GRAPH_CACHE[network_type]
    if os.path.exists(cache_file):
        print(f'  Граф {network_type} уже скачан, беру из файла...')
        return ox.load_graphml(cache_file)
    print(f'  Скачиваю граф {network_type} через OpenStreetMap...')
    try:
        G = ox.graph_from_bbox(north=north, south=south, east=east, west=west, network_type=network_type)
    except TypeError:
        G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type=network_type)
    ox.save_graphml(G, cache_file)
    return G

def main():
    points = load_points()
    ids = [p[0] for p in points]
    lats = [p[1] for p in points]
    lons = [p[2] for p in points]
    n = len(points)

    buf = 0.03
    north, south = max(lats) + buf, min(lats) - buf
    east, west = max(lons) + buf, min(lons) - buf

    result = {'ids': ids}
    profile_names = {'drive': 'driving-car', 'walk': 'foot-walking', 'bike': 'cycling-regular'}

    for net_type, out_name in profile_names.items():
        print(f'Профиль: {net_type}')
        G = get_graph(net_type, north, south, east, west)
        node_ids = ox.distance.nearest_nodes(G, X=lons, Y=lats)
        speed_ms = SPEED_KMH[net_type] * 1000 / 3600

        matrix = [[0] * n for _ in range(n)]
        dist_matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            lengths = nx.single_source_dijkstra_path_length(G, node_ids[i], weight='length')
            for j in range(n):
                if i == j:
                    continue
                dist_m = lengths.get(node_ids[j])
                if dist_m is None:
                    dist_m = 5000
                matrix[i][j] = round(dist_m / speed_ms + ACCESS_BUFFER_SEC)
                dist_matrix[i][j] = round(dist_m / 1000, 2)
        result[out_name] = matrix
        result[out_name + '_km'] = dist_matrix

    foot = result['foot-walking']
    result['public_transport'] = [[round(d/1.6) for d in row] for row in foot]

    with open('distance_matrix.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    print('Готово! Матрица сохранена в distance_matrix.json')

if __name__ == '__main__':
    main()

