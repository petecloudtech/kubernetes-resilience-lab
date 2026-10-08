FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv
COPY app/ /srv/app/
COPY tools/ /srv/tools/

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app.service"]
