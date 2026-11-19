FROM python:3.12-slim
WORKDIR /srv
COPY server.py .
RUN useradd --uid 10002 hook
USER hook
EXPOSE 9000
CMD ["python", "server.py"]
