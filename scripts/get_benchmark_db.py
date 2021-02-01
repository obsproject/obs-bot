# coding:utf-8
import json
import requests
import time

s = requests.session()
s.headers['x-requested-with'] = 'XMLHttpRequest'
s.headers['accept'] = 'application/json, text/javascript, */*; q=0.01'

# Originally based on some github gist or paste I do not have the link for anymore, sorry!


class PassMarkParser:
    def fetch_index_page(self):
        r = s.get('https://www.cpubenchmark.net/CPU_mega_page.html')
        headers=dict(referer='https://www.cpubenchmark.net/CPU_mega_page.html', authority='www.cpubenchmark.net')
        r = s.get('https://www.cpubenchmark.net/data/', params=dict(_=int(time.time()*1000)), headers=headers) 
        return r.json()

    def _null_if_na(self, text):
        ts = text.strip()
        if ts == '' or ts == "NA" or ts == "Not Available" or ts is None:
            return 0
        else:
            return text
    
    def parse_index_page(self, content):
        returned = []
        for d in content['data']:
            if d['name'] == 'NA' or d['name'] == 'Not Available' or not d['name']:
                print('No name:', d)
            returned.append({
                "id": int(d['id']),
                "name": d['name'],
                "name_lower": d['name'].strip().lower().replace('-', ' '),
                "kind": u"cpu",
                "other_names": [],
                "price": None,
                "cpu_mark": int(self._null_if_na(d['cpumark'].replace(',', ''))),
                "cpu_value": None,
                "cpu_st_mark": int(self._null_if_na(d['thread'].replace(',', ''))),
                "cpu_st_value": None,
                "tdp": None,
                "power_perf": None,
                "test_date": None,
                "cpu_socket": None,
                "cpu_category": d['cat'],
                "ext": {}
            })
        
        return returned

    def fetch_and_parse(self):
        return self.parse_index_page(self.fetch_index_page())


class GPUPassMarkParser:
    def _null_if_na(self, text):
        ts = text.strip()
        if ts == '' or ts == "NA" or ts == "Not Available" or ts is None:
            return 0
        else:
            return text

    def fetch_index_page(self):
        r = s.get('https://www.videocardbenchmark.net/GPU_mega_page.html')
        headers=dict(referer='https://www.videocardbenchmark.net/GPU_mega_page.html', authority='www.videocardbenchmark.net')
        r = s.get('https://www.videocardbenchmark.net/data/', params=dict(_=int(time.time()*1000)), headers=headers) 
        return r.json()

    def parse_index_page(self, content):
        returned = []
        for d in content['data']:
            if d['name'] == 'NA' or d['name'] == 'Not Available' or not d['name']:
                print('No name:', d)
            
            returned.append({
                "id": int(d['id']),
                "name": d['name'],
                "name_lower": d['name'].strip().lower().replace('/', ' '),
                "kind": u"gpu",
                "other_names": [],
                "price": None,
                "gpu_3d_mark": int(self._null_if_na(d['g3d'].replace(',', ''))),
                "gpu_value": None,
                "gpu_2d_mark": int(self._null_if_na(d['g2d'].replace(',', ''))),
                "tdp": None,
                "power_perf": None,
                "test_date": None,
                "gpu_category": d['cat'],
                "ext": {}
            })
        return returned

    def fetch_and_parse(self):
        return self.parse_index_page(self.fetch_index_page())


if __name__ == '__main__':
    p = PassMarkParser()
    data = p.fetch_and_parse()
    json.dump(data, open('../data/cpu_db.json', 'w'), indent=2)
    p = GPUPassMarkParser()
    data = p.fetch_and_parse()
    json.dump(data, open('../data/gpu_db.json', 'w'), indent=2)
