import os
from selenium.webdriver import ChromeOptions
from seleniumrequests import Chrome
import tempfile

from mintamazontagger.webdriver import get_stable_chrome_driver


def main():
    chrome_options = ChromeOptions()
    temp_dir = tempfile.TemporaryDirectory()
    # The follow line doesn't matter (doesn't seem to increase incident rate).
    # Added to give the chrome launch a clean slate.
    chrome_options.add_argument(f"user-data-dir={temp_dir.name}")
    home_dir = os.path.expanduser("~")
    webdriver = Chrome(options=chrome_options,
                       executable_path=get_stable_chrome_driver(home_dir))
    webdriver.get('https://www.google.com')

    # This fails about 1/3 times on Mac:
    response = webdriver.request(
        'GET',
        'https://www.google.com/images/branding/googlelogo/2x/googlelogo_light_color_272x92dp.png')
    response.raise_for_status()
    # Following is unncessary, but proves the point that the fetch was successful.
    with tempfile.NamedTemporaryFile() as fh:
        fh.write(response.content)
        print(fh.name)


if __name__ == '__main__':
    main()
