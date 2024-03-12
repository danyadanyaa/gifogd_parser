import json
import logging
import os
from datetime import datetime, timedelta, date

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from utils import get_driver


class UnauthorizedException(Exception):
    pass


class DataObject:
    def __init__(self):
        self.region: str = 'moscow'
        self.date: str = None
        self.number: str = None
        self.zastroychik: str = None
        self.cad_numbers: list = None
        self.details: dict = None
        self.teps: dict = None
        self.additional_teps: dict = None
        self.url: str = None
        self.cad_links: list = None
        self.tep_groups: dict = None
        self.additional_teps_groups: dict = None
        self.description: str = None
        self.fno: str = None


def get_cookies(email, password, proxy_):
    with get_driver(proxy=proxy_) as driver:
        driver.get('https://gisogd.mos.ru/')
        WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, '//button[@class="btn btn-primary"]'))
        )
        enter_but = driver.find_element(By.XPATH, '//button[@class="btn btn-primary"]')
        enter_but.click()
        WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, '//input[@name="login"]'))
        )
        mail_area = driver.find_element(By.XPATH, '//input[@name="login"]')
        mail_area.send_keys(email)
        pass_area = driver.find_element(By.XPATH, '//input[@name="password"]')
        pass_area.send_keys(password)
        button = driver.find_element(By.XPATH, '//button[@class="form-login__submit"]')
        button.click()
        WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.XPATH, '//button[contains(@class, "advanced-search-toggle")]'))
        )
        filter_but = driver.find_element(By.XPATH, '//button[contains(@class, "advanced-search-toggle")]')
        filter_but.click()
        cookies = driver.get_cookies()
        driver.quit()

        return cookies


def set_cookies(cookies):
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'],
                            secure=cookie['secure'], expires=cookie.get('expiry', None))


def extract_data(data):
    obj = DataObject()

    obj.date = data['dateOfDocument']
    obj.number = data['officialDocumentNumber']
    obj.address = data['address']

    if 'cadastralNumbers' in data:
        obj.cad_numbers = data['cadastralNumbers']

    detail_url = 'https://gisogd.mos.ru/isogd/front/api/gisogd/documents/{}/brief'

    with session.get(detail_url.format(data['id'])) as req:
        req.raise_for_status()
        add_data = req.json()

    custom_attr = json.loads(add_data['customAttributes'])
    other_details = {}
    for attr in custom_attr:
        if 'tepList' in attr['code']:
            tep_dict = {}
            attr_dict = json.loads(attr['value'])
            for element in attr_dict:
                if len(element) == 2:
                    name, value = element
                else:
                    continue
                if 'tepListTepName' in name:
                    tep_dict[value['value']] = name['value']
                else:
                    tep_dict[name['value']] = value['value']
            obj.teps = tep_dict
        elif 'dopTepList' in attr['code']:
            tep_dict = {}
            attr_dict = json.loads(attr['value'])
            for element in attr_dict:
                if len(element) == 2:
                    name, value = element
                else:
                    continue
                if 'tepListTepName' in name:
                    tep_dict[value['value']] = name['value']
                else:
                    tep_dict[name['value']] = value['value']
            obj.additional_teps = tep_dict
        elif 'tepGroups' in attr['code']:
            tep_groups = {}
            attr_dict = json.loads(attr['value'])
            for group in attr_dict:
                for det in group:
                    if 'tepGroupsGroupName' in det['code']:
                        name_tepgroup = det['value']
                    else:
                        group_det = {}
                        attr_list = json.loads(det['value'])
                        for tep in attr_list:
                            name, value = tep
                            if 'tepGroupsTepListTepValue' in name:
                                group_det[value['value']] = name['value']
                            else:
                                group_det[name['value']] = value['value']
                        tep_groups[name_tepgroup] = group_det
            obj.tep_groups = tep_groups
        elif 'dopTepGroups' in attr['code']:
            tep_groups = {}
            attr_dict = json.loads(attr['value'])
            for group in attr_dict:
                for det in group:
                    if 'dopTepGroupsGroupName' in det['code']:
                        name_tepgroup = det['value']
                    else:
                        group_det = {}
                        attr_list = json.loads(det['value'])
                        for tep in attr_list:
                            name, value = tep
                            if 'dopTepGroupsTepListTepName' in name:
                                group_det[value['value']] = name['value']
                            else:
                                group_det[name['value']] = value['value']
                        tep_groups[name_tepgroup] = group_det
            obj.additional_teps_groups = tep_groups
        else:
            other_details[attr['name']] = attr['value']

    obj.details = other_details

    links = []

    for data_object in add_data['dataObjects']:
        if 'name' in data_object:
            obj.description = data_object['name']
        if 'destination' in data_object:
            obj.fno = data_object['destination']
        for terrain in data_object['terrains']:
            for cad_link in terrain['cadastralNumbers']:
                if 'caseNumber' in cad_link:
                    links.append(f'https://gisogd.mos.ru/cases/{cad_link["caseNumber"]}')
                    with session.get(f'https://gisogd.mos.ru/isogd/front/api/gisogd/office-cases/{cad_link["caseNumber"]}/card') as req:
                        # Бывает отдает 504 ошибку
                        if req.status_code == 504:
                            continue
                        req.raise_for_status()
                        zastr_data = req.json()
                        if 'officeCase' in zastr_data:
                            if 'organisationName' in zastr_data['officeCase']:
                                zastroychik = req.json()['officeCase']['organisationName']
                                if zastroychik:
                                    obj.zastroychik = zastroychik

    if links:
        obj.cad_links = links
    obj.url = f'https://gisogd.mos.ru/document/{data["id"]}'

    return obj


def get_objects(type_, date=None):
    p_d = {
        "pagination":
            {
                "size": 100,
                "page": 0,
                "sortModel":
                    {
                        "field": "dateOfRegistration",
                        "order": "ASC"
                    }
            },
        "request": f"chapterCode:(\"{type_}\") AND dateOfDocument:[{date}T00:00:00.000Z TO *]"
    }

    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
        'Origin': 'https://gisogd.mos.ru',
        'Referer': 'https://gisogd.mos.ru/documents',

    }

    all_data = []
    while True:
        with session.post('https://gisogd.mos.ru/isogd/front/api/solr/docsSearch', json=p_d, headers=headers) as req:
            if req.status_code != 200:
                if req.status_code == 401:
                    raise UnauthorizedException()
                req.raise_for_status()
            all_json = req.json()
            objects = all_json['data']
            all_data.extend(objects)
            total = all_json['pagination']['total']
            if len(all_data) < total:
                p_d['pagination']['page'] += 1
            else:
                break
    return all_data


def is_valid_date(date_str, date_format="%Y-%m-%d"):
    try:
        parsed_date = datetime.strptime(date_str, date_format).date()
        today = date.today()
        return parsed_date <= today
    except ValueError:
        return False


def save_js_obj(obj):
    if obj.__dict__ not in loaded_objects:
        loaded_objects.append(obj.__dict__)


def parse():
    if not ARG_TYPE:
        raise Exception('Не задан TYPE')
    if not ARG_DATE_FROM:
        raise Exception('Не задан DATE_FROM')
    if ARG_TYPE not in ['GPZU', 'RNS']:
        raise Exception(f'Неверный тип документа {ARG_TYPE} (допустимо GPZU, RNS)')
    if ARG_DATE_FROM and not is_valid_date(ARG_DATE_FROM):
        raise Exception(f'Неверный формат даты {ARG_DATE_FROM}')

    type_ = {'GPZU': 'GPZU', 'RNS': 'RS'}[ARG_TYPE]
    date_obj = datetime.strptime(ARG_DATE_FROM, '%Y-%m-%d') - timedelta(days=1)
    date_ = date_obj.strftime('%Y-%m-%d')

    def _do_parse():
        cookies = get_cookies(EMAIL, PASSWORD, proxy)
        set_cookies(cookies)
        return get_objects(type_, date_)
    all_data = _do_parse()

    for doc in all_data:
        save_js_obj(extract_data(doc))

    print(json.dumps(loaded_objects, indent=4, ensure_ascii=False, sort_keys=False))


session = requests.Session()

ARG_TYPE = os.getenv('ARG_TYPE')
ARG_DATE_FROM = os.getenv('ARG_DATE_FROM', default=None)
ARG_PROXY = os.getenv('ARG_PROXY', default=None)

loaded_objects = []

EMAIL = 'email'
PASSWORD = 'password'

proxy = None
if ARG_PROXY:
    proxy = ARG_PROXY
    session.proxies = {
        'http': ARG_PROXY,
        'https': ARG_PROXY
    }

if __name__ == "__main__":
    parse()
