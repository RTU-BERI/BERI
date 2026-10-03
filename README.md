# BERI

BERI — сервис аренды товаров.

Пользователь может найти необходимый товар, забронировать его,
получить, использовать в течение определённого времени и вернуть
через сервис.

## Repository structure

backend/ — backend-приложение проекта

## Backend stack

- Python 3.13
- Django 5.2
- Django REST Framework

В следующих этапах:
- PostgreSQL

## Backend setup

Позже

### 1. Create virtual environment

python -m venv .venv

### 2. Activate environment

Windows PowerShell:

.venv\Scripts\Activate.ps1

macOS/Linux:

source .venv/bin/activate

### 3. Install dependencies

pip install -r backend/requirements.txt

### 4. Configure environment

Создать:

backend/.env

на основе:

backend/.env.example

### 5. Apply migrations

cd backend

python manage.py migrate

### 6. Run application

python manage.py runserver
