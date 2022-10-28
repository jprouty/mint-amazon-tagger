FROM ubuntu:22.10

LABEL maintainer="jeff.prouty@gmail.com"

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    wget

# Add Chrome repo.
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \ 
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list

# Install stable chrome.
RUN apt-get update && apt-get install -y \
    google-chrome-stable

RUN pip3 install --upgrade pip

COPY . /var/app
WORKDIR /var/app

RUN pip3 install -r /var/app/requirements/base.txt -r /var/app/requirements/ubuntu.txt

CMD ["python3", "-m", "mintamazontagger.cli", "--headless"]