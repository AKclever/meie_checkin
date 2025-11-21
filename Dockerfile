FROM python:3.12-slim

# töökaust konteineris
WORKDIR /app

# sõltuvused
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# rakenduse kood
COPY . .

# igaks juhuks
RUN mkdir -p /app/data

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# gunicorn käivitab app'i (app.py -> app = Flask(...))
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
