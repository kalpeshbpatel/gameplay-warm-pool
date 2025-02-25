# Use official Python image as base
FROM python:3.9

# Set default environment variables
ENV REGION=ap-south-1
ENV CLUSTER_NAME=scarfall-dev
ENV NODEGROUP_NAME=sf2-warmpool-20250225174150032500000002
ENV NAMESPACE=sf2-instance
ENV POD_PREFIX=ubuntu

# Resource Limits
ENV POD_CPU_LIMIT=0.5
ENV POD_MEMORY_LIMIT=512
ENV SERVER_CPU=2
ENV SERVER_MEMORY=2048
ENV PRE_WARM_MIN_CPU=2
ENV PRE_WARM_MIN_MEMORY=2048

# Scaling Configuration
ENV SLEEP_INTERVAL=15
ENV SCALE_DOWN_WAIT_TIME=120

# Set build argument for script selection
ENV SCRIPT_MODE=up-only

# Set working directory
WORKDIR /app

# Copy scripts and dependencies
COPY requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy Script
COPY warm-pool-up-only.py warm-pool-up-down.py ./

# Determine which script to run
CMD ["sh", "-c", "python warm-pool-up-only.py"]