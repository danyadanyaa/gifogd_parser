import os.path
import datetime
import tempfile
import zipfile
import psutil
import shutil
from contextlib import contextmanager
import json
import time
from math import ceil
from time import sleep
from urllib.parse import urljoin, parse_qs, urlencode
from pathlib import Path

from selenium.common import StaleElementReferenceException, ElementClickInterceptedException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc

def scroll_down(driver):
    n = 5
    prev_offset = driver.execute_script('return window.pageYOffset')
    offset_started = False
    while n:
        n -= 1
        driver.execute_script('window.scrollBy(0,document.body.scrollHeight)')
        offset = driver.execute_script('return window.pageYOffset')
        # Ждём пока не начнётся сдвиг
        if offset:
            if not offset_started:
                n = 5
                offset_started = True
            if offset == prev_offset:
                return
        prev_offset = offset
        time.sleep(0.5)

def is_collection(obj):
    """ Returns true for any iterable which is not a string or byte sequence.
    """
    if isinstance(obj, str):
        return False
    if isinstance(obj, bytes):
        return False
    if isinstance(obj, dict):
        return False
    try:
        iter(obj)
    except TypeError:
        return False
    try:
        hasattr(None, obj)
    except TypeError:
        return True
    return False

class Proxy():
    def __init__(self, addr, port, username, password):
        self.addr = addr
        self.port = port
        self.username = username
        self.password = password

    @classmethod
    def from_str(cls, item):
        item = item.replace('http://', '')
        item = item.replace('https://', '')
        if '@' in item:
            item = item.strip('/')
            up, ap = item.split('@')
            user, passw = up.split(':')
            addr, port = ap.split(':')
            return Proxy(addr=addr, port=int(port),
                         username=user,
                         password=passw)
        if len(item.split(':')) == 4:
            addr, port, user, passw = item.split(':')
            return Proxy(addr=addr, port=int(port),
                         username=user,
                         password=passw)
        if len(item.split(':')) == 2:
            addr, port = item.split(':')
            return Proxy(addr=addr, port=int(port),
                         username=None,
                         password=None)
        raise Exception()


@contextmanager
def use_proxy_extension(options, proxy=None, use_load_extension_dir=False):
    """
    use_load_extension_dir - тольео через with, иначе директория с расширением сразу удалиться
    Добавляет в расширение для настройки прокси с указанием логина и пароля для подключения. Важно для отдельного
    инстанса всегда использовать данное расширение, т.к. в случае обращения через прокси с подключенным расширением
    и последующим обращением с отключенным расширением (без прокси) в настройках Chrome (profile/Default/Preferences)
    будет сохранен использованный прокси и вместо прямого подключения хром будет выполнять попытку подключения через него.
    Подключение плагина и указание proxy=None - решает данную проблему изменяя настройки на прямое подключение.
    :param options: опции webdriver
    :param proxy: объект прокси. Если None выполняется прямое подключение без использования прокси.
    """
    manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
        		"notifications",
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
    """

    without_proxy_js = """
        chrome.proxy.settings.set({value: null, scope: "regular"}, function() {});
    """

    with_proxy_js = """
        var config = {
                mode: "fixed_servers",
                rules: {
                  singleProxy: { scheme: "%scheme%", host: "%hostname%", port: parseInt(%port%)},
                  bypassList: ["localhost"]
                }
              };
        chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

        function callbackFn(details) {
            return {
                authCredentials: { username: "%username%", password: "%password%"}
            };
        }
        chrome.webRequest.onAuthRequired.addListener(
                    callbackFn, 
                    {urls: ["<all_urls>"]},
                    ['blocking']
        );
    """
    if isinstance(proxy, str):
        proxy = Proxy.from_str(proxy)
    if proxy:
        proxy_url = f'http://{proxy.addr}:{proxy.port}'
        options.add_argument(f'--proxy-server={proxy_url}')

        js = with_proxy_js
        js = js.replace('%scheme%', 'http')
        js = js.replace('%hostname%', proxy.addr)
        js = js.replace('%port%', str(proxy.port))

        if proxy.username:
            js = js.replace('%username%', proxy.username)
        else:
            js = js.replace('%username%', '')

        if proxy.password:
            js = js.replace('%password%', proxy.password)
        else:
            js = js.replace('%password%', '')

    else:
        js = without_proxy_js

    if use_load_extension_dir:
        # с undetected не работает add_extension, надо через директорию https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/349
        with tempfile.TemporaryDirectory() as tmpdirname:
            with open(os.path.join(tmpdirname, 'manifest.json'), mode='w') as f:
                f.write(manifest_json)
            with open(os.path.join(tmpdirname, 'background.js'), mode='w') as f:
                f.write(js)
            options.add_argument(f"--load-extension={tmpdirname}")
            yield options
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        with zipfile.ZipFile(tmp, 'w') as zp:
            zp.writestr("manifest.json", manifest_json)
            zp.writestr("background.js", js)

        options.add_extension(tmp.name)

def class_startswith_locator(string):
    return (By.XPATH, f'//div[starts-with(@class, "{string}")]')

class find_elements_with_text(object):
    def __init__(self, locator, text_):
        self.locator = locator
        self.text = text_

    def __call__(self, driver):
        try:
            r = []
            for el in driver.find_elements(*self.locator):
                element_text = el.text
                if self.text in element_text:
                    r.append(el)
            if r:
                return r
            return []
        except StaleElementReferenceException:
            return []


class find_element_with_text(object):
    def __init__(self, locator, text_):
        self.locator = locator
        self.text = text_

    def __call__(self, driver):
        try:
            for el in driver.find_elements(*self.locator):
                element_text = el.text
                if self.text in element_text:
                    return el
            return None
        except StaleElementReferenceException:
            return None

class find_element_exact_text(object):
    def __init__(self, locator, text_):
        self.locator = locator
        self.text = text_

    def __call__(self, driver):
        try:
            for el in driver.find_elements(*self.locator):
                element_text = el.text
                if element_text and self.text == element_text.strip():
                    return el
            return None
        except StaleElementReferenceException:
            return None

class AnyEc:
    """ Use with WebDriverWait to combine expected_conditions
        in an OR.
    """

    def __init__(self, *args):
        if len(args) == 1 and is_collection(args[0]):
            self.ecs = args[0]
        else:
            self.ecs = args

    def __call__(self, driver):
        for fn in self.ecs:
            if not fn:
                continue
            try:
                if fn(driver): return fn
            except:
                pass

#
# Programmatically detect the version of the Chrome web browser installed on the PC.
# Compatible with Windows, Mac, Linux.
# Written in Python.
# Uses native OS detection. Does not require Selenium nor the Chrome web driver.
#

import os
import re
from sys import platform

def extract_version_registry(output):
    try:
        google_version = ''
        for letter in output[output.rindex('DisplayVersion    REG_SZ') + 24:]:
            if letter != '\n':
                google_version += letter
            else:
                break
        return(google_version.strip())
    except TypeError:
        return

def extract_version_folder():
    # Check if the Chrome folder exists in the x32 or x64 Program Files folders.
    for i in range(2):
        path = 'C:\\Program Files' + (' (x86)' if i else '') +'\\Google\\Chrome\\Application'
        if os.path.isdir(path):
            paths = [f.path for f in os.scandir(path) if f.is_dir()]
            for path in paths:
                filename = os.path.basename(path)
                pattern = '\d+\.\d+\.\d+\.\d+'
                match = re.search(pattern, filename)
                if match and match.group():
                    # Found a Chrome version.
                    return match.group(0)

    return None

def get_chrome_version():
    version = None
    install_path = None

    try:
        if platform == "linux" or platform == "linux2":
            # linux
            install_path = "/usr/bin/google-chrome"
        elif platform == "darwin":
            # OS X
            install_path = "/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome"
        elif platform == "win32":
            # Windows...
            try:
                # Try registry key.
                stream = os.popen('reg query "HKLM\\SOFTWARE\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome"')
                output = stream.read()
                version = extract_version_registry(output)
            except Exception as ex:
                # Try folder path.
                version = extract_version_folder()
    except Exception as ex:
        print(ex)

    version = os.popen(f"{install_path} --version").read().strip('Google Chrome ').strip() if install_path else version

    return version


def get_profile_dir(proxy):
    """ 
    Возвращает путь к директории profile с учетом используемого прокси
    чтобы один и тотже профиль использовался в пределах одного прокси
    """
    profiles_dir = os.getenv('DEBUG_PROFILE_DIR', '/home/selenium/profiles')
    current_profile_dir = 'without_proxy'
    if proxy:
        if isinstance(proxy, str):
            proxy = Proxy.from_str(proxy)
            ip = proxy.addr
            port =proxy.port
        else:
            ip, port, *_ = proxy.split(':')
        current_profile_dir = f'{ip.replace(".", "_")}p{port}' 
    return f'{profiles_dir}/{current_profile_dir}'


def clear_undetected_chrome(profile_dir):
    """ 
    Выполняет очистку парсера на базе undetected_chromedriver 
    1. Убивает все запущенные процессы Chrome
    2. Очищает созданные undetected_chrome драйверы в ~/ .local/share/undetected_chromedriver
    """
    # Убиваем процесс Chrome
    processes = [proc for proc in psutil.process_iter(['pid', 'name']) if 'chrome' in proc.name()]
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    _, alive = psutil.wait_procs(processes, timeout=3)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass

    # Очищаем директорию с undetected_chromedriver
    path = '/home/selenium/.local/share/undetected_chromedriver/'
    # Она может быть не создана, например если контейнер был пересобран
    if os.path.exists(path):
        for file_name in os.listdir(path):
            file = path + file_name
            if os.path.isfile(file):
                os.remove(file)

    if profile_dir:
        # Удаляем в профиле директорию Default/Web Data т.к. она держит профиль
        #web_data_dir = f'{profile_dir}/Default/Web Data'
        #shutil.rmtree(web_data_dir, ignore_errors=True)

        # Удаляем в профиле файл SingletoneLock для разблокировки
        for filename in Path(profile_dir).glob("Singleton*"):
            filename.unlink()


def log_event(event, proxy, url):
    return
    """ Записывет в лог файл команду, время ее выполнения, контейнер ... """
    log_file = '/home/selenium/profiles/domclick_parser.log'
    try: 
        start_time = datetime.datetime.now()
        container_name = 'Undefined'
        with open('/etc/hostname', 'r') as f:
            container_name = f.read().strip()
        with open(log_file, 'a') as f:
            f.write(f'{start_time}: {container_name} {event} with proxy: {proxy}, {url}\n')
            f.flush()
    except Exception:
        pass


@contextmanager
def get_driver(proxy, profile_dir=None):
    """ Возвращает драйвер с обходом блокировки по детектированию selenium """
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument('--start-maximized')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-site-isolation-trials')
    chrome_options.add_argument("--disable-gpu")
    additional_kwargs = {}
    if profile_dir:
        additional_kwargs['user_data_dir'] = profile_dir
    version = get_chrome_version()
    with use_proxy_extension(chrome_options, proxy, use_load_extension_dir=True):
        driver = uc.Chrome(
            options=chrome_options,
            version_main=int(version.split('.')[0]),
            **additional_kwargs
        )
        yield driver
        driver.quit()
