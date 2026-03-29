# Apex Advisor Backend

Apex Advisor is a workplace adaptation assistant that helps users systematically organize workplace information and automatically generate AI prompt templates for specific scenarios.

## Features

- **Four-Layer Profile Architecture**: Team, Company, Work, and Industry profiles
- **AI-Powered Insights**: Specialized AI modules for behavioral profiling, intelligence classification, and prompt generation
- **JWT Authentication**: Secure user authentication with access and refresh tokens
- **RESTful API**: FastAPI-based backend with automatic OpenAPI documentation
- **Database Integration**: PostgreSQL with SQLAlchemy ORM and Alembic migrations

## Quick Start

### Using Docker (Recommended)

1. Clone the repository
2. Copy environment variables:
   ```bash
   cp .env.example .env
   ```
3. Start the services:
   ```bash
   docker-compose up -d
   ```
4. Run database migrations:
   ```bash
   docker-compose exec app alembic upgrade head
   ```

The API will be available at `http://localhost:8000`

### Manual Setup

1. Install Python 3.11+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up PostgreSQL database
4. Copy and configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your database credentials and API keys
   ```
5. Run database migrations:
   ```bash
   alembic upgrade head
   ```
6. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```

## API Documentation

Once the server is running, visit:
- Interactive API docs: `http://localhost:8000/docs`
- ReDoc documentation: `http://localhost:8000/redoc`

## Project Structure

```
app/
├── api/                 # API routes and endpoints
│   └── v1/             # API version 1
├── core/               # Core configuration and utilities
├── db/                 # Database configuration
├── models/             # SQLAlchemy models
├── schemas/            # Pydantic schemas
└── services/           # Business logic services
    └── ai/             # AI service modules
tests/                  # Test files
alembic/               # Database migrations
```

## Environment Variables

Key environment variables (see `.env.example` for full list):

- `DATABASE_URL`: PostgreSQL connection string
- `SECRET_KEY`: JWT signing key
- `OPENAI_API_KEY`: OpenAI API key for AI features
- `DEBUG`: Enable debug mode

## Testing

Run tests with pytest:
```bash
pytest
```

## Development

### Database Migrations

Create a new migration:
```bash
alembic revision --autogenerate -m "Description of changes"
```

Apply migrations:
```bash
alembic upgrade head
```

### Code Formatting

Format code with black:
```bash
black app/ tests/
```

Sort imports with isort:
```bash
isort app/ tests/
```

## Architecture

The system follows a layered architecture:

1. **API Layer**: FastAPI routes and request/response handling
2. **Service Layer**: Business logic and AI module orchestration
3. **Data Layer**: SQLAlchemy models and database operations
4. **AI Layer**: Specialized modules for intelligent processing

## License

This project is proprietary software. All rights reserved.