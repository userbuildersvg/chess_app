#!/bin/bash

# Development script for local Docker environment
# Usage: ./dev.sh [start|stop|restart|rebuild|logs|clean]

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Function to check if Docker is running
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker is not running! Please start Docker first."
        exit 1
    fi
}

# Function to check if Langflow is running
check_langflow() {
    print_step "Checking if Langflow is running on localhost:7860..."
    
    if curl -s http://localhost:7860/health > /dev/null 2>&1; then
        print_status "✅ Langflow is running on localhost:7860"
        return 0
    else
        print_warning "⚠️ Langflow is not running on localhost:7860"
        print_status "Please start Langflow first:"
        print_status "  langflow run --host 127.0.0.1 --port 7860"
        return 1
    fi
}

# Function to start services
start_services() {
    print_status "Starting Chess AI Platform..."
    
    check_docker
    
    if ! check_langflow; then
        print_error "Cannot start without Langflow. Please start Langflow first."
        exit 1
    fi
    
    print_step "Building and starting application with docker-compose..."
    docker-compose up --build -d
    
    print_status "🎉 Chess AI Platform started successfully!"
    echo ""
    print_status "🌐 Access Points:"
    print_status "  Frontend: http://localhost:3000"
    print_status "  Backend API: http://localhost:8080"
    print_status "  API Docs: http://localhost:8080/docs"
    print_status "  Langflow: http://localhost:7860"
    echo ""
    print_status "📝 To view logs: ./dev.sh logs"
    print_status "🛑 To stop: ./dev.sh stop"
}

# Function to stop services
stop_services() {
    print_step "Stopping Chess AI Platform..."
    docker-compose down
    print_status "Chess AI Platform stopped"
}

# Function to restart services
restart_services() {
    print_step "Restarting Chess AI Platform..."
    stop_services
    start_services
}

# Function to rebuild services
rebuild_services() {
    print_step "Rebuilding Chess AI Platform..."
    docker-compose down
    docker-compose up --build -d
    print_status "Chess AI Platform rebuilt and started"
}

# Function to show logs
show_logs() {
    print_status "Showing application logs..."
    docker-compose logs -f
}

# Function to clean everything
clean_all() {
    print_step "Cleaning all Docker resources..."
    
    docker-compose down
    docker system prune -f
    docker volume prune -f
    
    print_status "All Docker resources cleaned"
}

# Main script logic
case "$1" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    rebuild)
        rebuild_services
        ;;
    logs)
        show_logs
        ;;
    clean)
        clean_all
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|rebuild|logs|clean}"
        echo ""
        echo "Commands:"
        echo "  start    - Start Chess AI Platform"
        echo "  stop     - Stop Chess AI Platform"
        echo "  restart  - Restart Chess AI Platform"
        echo "  rebuild  - Rebuild and start Chess AI Platform"
        echo "  logs     - Show application logs"
        echo "  clean    - Remove all Docker resources"
        echo ""
        echo "Prerequisites:"
        echo "  - Docker and Docker Compose"
        echo "  - Langflow running on localhost:7860"
        echo ""
        echo "To start Langflow:"
        echo "  langflow run --host 127.0.0.1 --port 7860"
        echo ""
        echo "Access points after start:"
        echo "  Frontend: http://localhost:3000"
        echo "  Backend API: http://localhost:8080"
        echo "  API Docs: http://localhost:8080/docs"
        echo "  Langflow: http://localhost:7860"
        exit 1
        ;;
esac