from flask import Flask, render_template, request
import requests
import folium
from geopy.distance import geodesic
from rtree import index as rtree_index

app = Flask(__name__)

class OverpassAPI:
    def __init__(self, city):
        self.city = city
        self.overpass_url = "http://overpass-api.de/api/interpreter"
        self.nodes = {}
        self.power_objects = []
        self.ways = []
        self.residential_nodes = []

    def fetch_power_objects(self, power_line, communication_tower, substation, transformer, converter):
        query = f"""
                       [out:json][timeout:25];
                       area[name="{self.city}"]->.searchArea;
                       ("""
        if power_line or communication_tower or substation or transformer or converter:
            if power_line:
                query += 'way["power"="line"](area.searchArea);'
            if communication_tower:
                query += 'node["man_made"="tower"]["tower:type"="communication"](area.searchArea);'
            if substation:
                query += 'node["power"="substation"](area.searchArea);'
            if transformer:
                query += 'node["power"="transformer"](area.searchArea);'
            if converter:
                query += 'node["power"="converter"](area.searchArea);'
        else:
            query += 'node["place"="city"](area.searchArea);'  # Default query if nothing is selected

        query += """);
                       out body;>;
                       out skel qt;"""

        response = requests.get(self.overpass_url, params={'data': query})
        data = response.json()

        for element in data['elements']:
            if element['type'] == 'node':
                self.nodes[element['id']] = (element['lat'], element['lon'])
                tags = element.get('tags', {})
                if 'man_made' in tags and tags.get('tower:type') == 'communication':
                    self.power_objects.append((element['id'], 'Communication Tower'))
                if 'power' in tags:
                    self.power_objects.append((element['id'], tags['power'].capitalize()))
            elif element['type'] == 'way':
                self.ways.append(element)

        print(len(self.power_objects))
        print(len(self.ways))

    def fetch_buildings(self):
        query = f"""
                        [out:json][timeout:25];
                        area[name="{self.city}"]->.searchArea;
                        (
                          way["building"](area.searchArea);
                          node(w)->.x;
                        );
                        out body;
                        >;
                        out skel qt;"""

        response = requests.get(self.overpass_url, params={'data': query})
        data = response.json()

        for element in data['elements']:
            if element['type'] == 'node':
                tags = element.get('tags', {})
                self.residential_nodes.append(element)

        print(len(self.residential_nodes))


class MapCreator:
    def __init__(self, nodes, power_objects, ways, residential_nodes):
        self.nodes = nodes
        self.power_objects = power_objects
        self.ways = ways
        self.residential_nodes = residential_nodes

    def create_map_optimized(self):
        # Map initialization
        if self.nodes:
            center_lat = sum(lat for lat, lon in self.nodes.values()) / len(self.nodes)
            center_lon = sum(lon for lat, lon in self.nodes.values()) / len(self.nodes)
            map_osm = folium.Map(location=[center_lat, center_lon], zoom_start=12)
        else:
            return folium.Map(location=[0, 0], zoom_start=2)

        # Adding markers for power objects as circles
        colors = {'Substation': 'blue', 'Transformer': 'green', 'Converter': 'orange', 'Communication Tower': 'red'}
        distances = {'Communication Tower': 45, 'Substation': 10, 'Transformer': 10, 'Converter': 10}

        # Create spatial index for residential nodes
        res_index = rtree_index.Index()
        for pos, res_node in enumerate(self.residential_nodes):
            res_lat, res_lon = res_node['lat'], res_node['lon']
            res_index.insert(pos, (res_lon, res_lat, res_lon, res_lat))

        for node_id, type_ in self.power_objects:
            lat, lon = self.nodes[node_id]
            folium.CircleMarker(
                location=[lat, lon],
                radius=10,
                color=colors.get(type_, 'gray'),
                fill=True,
                fill_color=colors.get(type_, 'gray'),
                popup=f'{type_}'
            ).add_to(map_osm)

            # Check for nearby residential buildings using spatial index
            nearby_res_nodes = list(res_index.intersection((lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)))
            for idx in nearby_res_nodes:
                res_node = self.residential_nodes[idx]
                res_lat, res_lon = res_node['lat'], res_node['lon']
                distance = geodesic((lat, lon), (res_lat, res_lon)).meters
                if distance <= distances[type_]:
                    folium.Marker(
                        location=[res_lat, res_lon],
                        icon=folium.Icon(color='red', icon='exclamation-sign'),
                        popup='Residential Building near Power Object'
                    ).add_to(map_osm)

        # Adding power lines with varying thickness based on voltage
        def get_thickness(voltage):
            if not voltage:
                return 40  # Default value when voltage is missing
            try:
                voltages = list(map(int, voltage.split(';')))
                max_voltage = max(voltages)
            except ValueError:
                return 20  # Default value when voltage format is incorrect
            if max_voltage < 1000:
                return 2
            elif max_voltage < 20000:
                return 10
            elif max_voltage == 35000:
                return 15
            elif max_voltage == 110000:
                return 20
            elif max_voltage in [150000, 220000]:
                return 25
            elif max_voltage in [330000, 400000, 500000]:
                return 30
            elif max_voltage == 750000:
                return 40
            elif max_voltage == 1150000:
                return 55
            return 20

        for way in self.ways:
            points = [self.nodes[node_id] for node_id in way['nodes'] if node_id in self.nodes]

            # Check for nearby residential buildings along the line using spatial index
            for i in range(len(points) - 1):
                start_point = points[i]
                end_point = points[i + 1]
                nearby_res_nodes = list(res_index.intersection((min(start_point[1], end_point[1]) - 0.01,
                                                                min(start_point[0], end_point[0]) - 0.01,
                                                                max(start_point[1], end_point[1]) + 0.01,
                                                                max(start_point[0], end_point[0]) + 0.01)))
                for idx in nearby_res_nodes:
                    res_node = self.residential_nodes[idx]
                    res_lat, res_lon = res_node['lat'], res_node['lon']
                    distance_to_line_segment = min(
                        geodesic(start_point, (res_lat, res_lon)).meters,
                        geodesic(end_point, (res_lat, res_lon)).meters
                    )
                    voltage = way.get('tags', {}).get('voltage', '')
                    thickness = get_thickness(voltage)
                    if distance_to_line_segment <= thickness * distances.get('power_line', 1):
                        folium.Marker(
                            location=[res_lat, res_lon],
                            icon=folium.Icon(color='red', icon='exclamation-sign'),
                            popup='Residential Building near Power Line'
                        ).add_to(map_osm)

            voltage = way.get('tags', {}).get('voltage', '')
            thickness = get_thickness(voltage)

            folium.PolyLine(points, color="blue", weight=2.5, opacity=1).add_to(map_osm)

        return map_osm


@app.route('/', methods=['GET', 'POST'])
def index():
    city = 'Волгоград'  # Default value
    options = {
        'power_line': False,
        'communication_tower': False,
        'substation': False,
        'transformer': False,
        'converter': False
    }
    if request.method == 'POST':
        city = request.form.get('city', city)
        for key in options.keys():
            options[key] = key in request.form

    overpass_api = OverpassAPI(city)
    overpass_api.fetch_power_objects(**options)
    if any(options.values()):
        overpass_api.fetch_buildings()

    map_creator = MapCreator(overpass_api.nodes, overpass_api.power_objects, overpass_api.ways, overpass_api.residential_nodes)
    map_osm = map_creator.create_map_optimized()
    map_osm.save('static/map.html')

    return render_template('index.html', city=city, options=options)


if __name__ == '__main__':
    app.run(debug=True)