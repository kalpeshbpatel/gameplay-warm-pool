import time
import boto3
import math
import os
import logging
from kubernetes import client, config

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load in-cluster Kubernetes config (EKS Service Account)
try:
    config.load_incluster_config()
except config.ConfigException:
    logger.error("Cannot load in-cluster Kubernetes configuration")
    exit(1)

# AWS Configuration (Using IAM Role for Service Account)
CLUSTER_NAME = os.getenv("EKS_CLUSTER_NAME", "scarfall-dev")
NODEGROUP_NAME = os.getenv("EKS_NODEGROUP_NAME", "sf2-warmpool-20250225174150032500000002")
REGION = os.getenv("AWS_REGION", "ap-south-1")

# Kubernetes Configuration
NAMESPACE = os.getenv("K8S_NAMESPACE", "sf2-instance")
POD_PREFIX = os.getenv("POD_PREFIX", "ubuntu")  # Only count pods with this prefix

# Resource Limits
POD_CPU_LIMIT = float(os.getenv("POD_CPU_LIMIT", 0.5))  # CPU limit per pod
POD_MEMORY_LIMIT = int(os.getenv("POD_MEMORY_LIMIT", 512))  # Memory limit per pod (MB)
SERVER_CPU = int(os.getenv("SERVER_CPU", 2))  # CPU cores per server
SERVER_MEMORY = int(os.getenv("SERVER_MEMORY", 2048))  # Memory per server (MB)
PRE_WARM_MIN_CPU = int(os.getenv("PRE_WARM_MIN_CPU", 2))  # Minimum CPU for pre-warming
PRE_WARM_MIN_MEMORY = int(os.getenv("PRE_WARM_MIN_MEMORY", 2048))  # Minimum Memory for pre-warming

# Scaling Configuration
SLEEP_INTERVAL = int(os.getenv("SLEEP_INTERVAL", 15))  # Interval in seconds to check pod count
SCALE_DOWN_WAIT_TIME = int(os.getenv("SCALE_DOWN_WAIT_TIME", 120))  # Wait time before scaling down

# Create EKS client using IAM role
session = boto3.Session()
eks_client = session.client("eks", region_name=REGION)

downscale_start_time = None

def get_current_desired_size():
    try:
        response = eks_client.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODEGROUP_NAME)
        desired_size = response["nodegroup"]["scalingConfig"]["desiredSize"]
        logger.info(f"Current desired size from EKS: {desired_size}")
        return desired_size
    except Exception as e:
        logger.error(f"Error fetching EKS node group details: {e}")
        return 1

def get_pod_count():
    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(namespace=NAMESPACE)
        filtered_pods = [p.metadata.name for p in pods.items if p.metadata.name.startswith(POD_PREFIX)]
        return len(filtered_pods)
    except Exception as e:
        logger.error(f"Error fetching pods: {e}")
        return 0

def update_eks_nodegroup(desired_size):
    logger.info(f"Updating EKS node group '{NODEGROUP_NAME}' to desired size: {desired_size}")
    try:
        response = eks_client.update_nodegroup_config(
            clusterName=CLUSTER_NAME,
            nodegroupName=NODEGROUP_NAME,
            scalingConfig={"desiredSize": desired_size}
        )
        logger.info(f"Update request successful: {response}")
    except Exception as e:
        logger.error(f"Error updating EKS node group: {e}")

def calculate_desired_size(pod_count, current_desired_size):
    required_cpu = (pod_count * POD_CPU_LIMIT) + PRE_WARM_MIN_CPU
    required_memory = (pod_count * POD_MEMORY_LIMIT) + PRE_WARM_MIN_MEMORY
    desired_size_cpu = math.ceil(required_cpu / SERVER_CPU)
    desired_size_memory = math.ceil(required_memory / SERVER_MEMORY)
    new_desired_size = max(desired_size_cpu, desired_size_memory)
    if new_desired_size != current_desired_size:
        logger.info(f"Pod Count: {pod_count}, Required CPU: {required_cpu}, Required Memory: {required_memory}")
        logger.info(f"Desired Size (CPU): {desired_size_cpu}, Desired Size (Memory): {desired_size_memory}")
        logger.info(f"New Desired Size: {new_desired_size}, Current Desired Size: {current_desired_size}")
    return new_desired_size

def main():
    global downscale_start_time
    try:
        while True:
            current_desired_size = get_current_desired_size()
            pod_count = get_pod_count()
            logger.info(f"Found {pod_count} pods in namespace {NAMESPACE}")
            new_desired_size = calculate_desired_size(pod_count, current_desired_size)
            if new_desired_size < current_desired_size:
                if downscale_start_time is None:
                    downscale_start_time = time.time()
                    logger.info(f"Waiting {SCALE_DOWN_WAIT_TIME} seconds before applying scale down...")
                else:
                    elapsed_time = time.time() - downscale_start_time
                    remaining_time = int(SCALE_DOWN_WAIT_TIME - elapsed_time)
                    if remaining_time > 0:
                        logger.info(f"Scaling down in {remaining_time} seconds...")
                    if elapsed_time >= SCALE_DOWN_WAIT_TIME:
                        logger.info(f"Applying downscale to {new_desired_size} after waiting {SCALE_DOWN_WAIT_TIME} seconds.")
                        update_eks_nodegroup(new_desired_size)
                        downscale_start_time = None
            else:
                if new_desired_size > current_desired_size:
                    update_eks_nodegroup(new_desired_size)
                downscale_start_time = None
            time.sleep(SLEEP_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Script interrupted. Exiting gracefully...")

if __name__ == "__main__":
    main()