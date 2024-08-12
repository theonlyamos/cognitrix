FROM ubuntu:22.04

LABEL maintainer="Amos Amissah"

ENV TZ=UTC

WORKDIR /app/

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update \
    && apt-get install -y gnupg gosu curl ca-certificates zip unzip git supervisor \
       sqlite3 libcap2-bin libpng-dev python3.11 python3.11-dev python3.11-venv \
    && curl https://bootstrap.pypa.io/get-pip.py | python3.11

    && apt-get -y autoremove \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

RUN curl https://bun.sh/install | bash 

RUN echo 'export BUN_PATH="$HOME/.bun"' >> $HOME/.bashrc \
    && echo 'export PATH="$PATH:$BUN_PATH"' >> $HOME/.bashrc \
    && . $HOME/.bashrc \
    && bun add -g dotenv

RUN python3.11 -m pip install python-dotenv

RUN ln -s /usr/bin/python

RUN ln -s /usr/bin/python3.11 /usr/bin/python  

COPY . /app/

RUN mv .env.example .env

RUN cd /app/frontend && bun run build

RUN pip install -e .

EXPOSE 9000

# RUN ln -sf /bin/bash /bin/sh

ENTRYPOINT ["runit-server"]
