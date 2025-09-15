import time
import boto3
import math
import os
import logging
from kubernetes import client, config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load in-cluster Kubernetes config (EKS Service Account)
try:
    config.load_incluster_config()
    logging.info("Successfully loaded in-cluster Kubernetes configuration.")
except config.ConfigException:
    logging.error("Error: Cannot load in-cluster Kubernetes configuration.")
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
POD_MEMORY_LIMIT = float(os.getenv("POD_MEMORY_LIMIT", 512))  # Memory limit per pod (MB)
SERVER_CPU = float(os.getenv("SERVER_CPU", 2))  # CPU cores per server
SERVER_MEMORY = float(os.getenv("SERVER_MEMORY", 2048))  # Memory per server (MB)
PRE_WARM_POD_SIZE = int(os.getenv("PRE_WARM_POD_SIZE", 2))  # Minimum pods for pre-warming

# Scaling Configuration
SLEEP_INTERVAL = int(os.getenv("SLEEP_INTERVAL", 15))  # Interval in seconds to check pod count
USE_EC2_COUNT = os.getenv("USE_EC2_COUNT", "true").lower() == "true"  # Use EC2 instance count instead of EKS desired size

# Create AWS clients using default IAM role (via service account)
session = boto3.Session()
eks_client = session.client("eks", region_name=REGION)
ec2_client = session.client("ec2", region_name=REGION)

def get_current_ec2_node_count():
    """Fetches the current count of EC2 instances based on EKS cluster and nodegroup tags."""
    try:
        # Define filters for EC2 instances with specific tags
        filters = [
            {
                'Name': 'tag:eks:cluster-name',
                'Values': [CLUSTER_NAME]
            },
            {
                'Name': 'tag:eks:nodegroup-name',
                'Values': [NODEGROUP_NAME]
            },
            {
                'Name': 'instance-state-name',
                'Values': ['running', 'pending']
            }
        ]
        
        response = ec2_client.describe_instances(Filters=filters)
        
        running_count = 0
        pending_count = 0
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                state = instance['State']['Name']
                if state == 'running':
                    running_count += 1
                elif state == 'pending':
                    pending_count += 1
        
        total_count = running_count + pending_count
        logging.info(f"Current EC2 nodes - Running: {running_count}, Pending: {pending_count}, Total: {total_count}")
        return total_count, running_count, pending_count
        
    except Exception as e:
        logging.error(f"Error fetching EC2 instances: {e}")
        return 1, 1, 0  # Default to 1 running, 0 pending if unable to fetch

def get_current_desired_size():
    """Fetches the current desired size of the EKS node group."""
    try:
        response = eks_client.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODEGROUP_NAME)
        desired_size = response["nodegroup"]["scalingConfig"]["desiredSize"]
        logging.info(f"Current desired size from EKS: {desired_size}")
        return desired_size
    except Exception as e:
        logging.error(f"Error fetching EKS node group details: {e}")
        return 1  # Default to 1 if unable to fetch

def get_current_node_count():
    """Gets current node count based on USE_EC2_COUNT environment variable."""
    if USE_EC2_COUNT:
        logging.info("Using EC2 instance count for node counting")
        total_nodes, running_nodes, pending_nodes = get_current_ec2_node_count()
        return total_nodes, running_nodes, pending_nodes
    else:
        logging.info("Using EKS node group desired size for node counting")
        desired_size = get_current_desired_size()
        return desired_size, desired_size, 0  # Return as (total, running, pending) format

def get_pod_count():
    """Fetches the count of pods matching the defined prefix in the specified namespace."""
    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(namespace=NAMESPACE)
        filtered_pods = [p.metadata.name for p in pods.items if p.metadata.name.startswith(POD_PREFIX)]
        return len(filtered_pods)
    except Exception as e:
        logging.error(f"Error fetching pods: {e}")
        return 0

def update_eks_nodegroup(desired_size):
    """Updates only the desired size of the EKS node group."""
    logging.info(f"Updating EKS node group '{NODEGROUP_NAME}' in cluster '{CLUSTER_NAME}':")
    logging.info(f"  - New Desired Size: {desired_size}")
    try:
        response = eks_client.update_nodegroup_config(
            clusterName=CLUSTER_NAME,
            nodegroupName=NODEGROUP_NAME,
            scalingConfig={"desiredSize": desired_size}  # Only updating desiredSize
        )
        logging.info(f"Update request sent successfully: {response}")
    except Exception as e:
        logging.error(f"Error updating EKS node group: {e}")

def calculate_desired_size(pod_count, current_node_count):
    """Calculates the new desired size based on CPU and memory requirements."""
    pod_per_server = min(
        math.floor(SERVER_CPU / POD_CPU_LIMIT),
        math.floor(SERVER_MEMORY / POD_MEMORY_LIMIT)
    )
    
    new_desired_size = math.ceil((pod_count + PRE_WARM_POD_SIZE) / pod_per_server)
    
    if new_desired_size > current_node_count:
        logging.info("=== Desired Size Calculation ===================================")
        logging.info(f"  - Namespace: {NAMESPACE}")
        logging.info(f"  - Pod Prefix: {POD_PREFIX}")
        logging.info(f"  - Pod Count: {pod_count}")
        logging.info(f"  - Warm Pod Count: {PRE_WARM_POD_SIZE}")
        logging.info(f"  - Pods per Server: {pod_per_server}")
        logging.info(f"  - Current Node Count: {current_node_count}")
        logging.info(f"  - Final New Desired Size: {new_desired_size}")
        logging.info("================================================================")
        return new_desired_size
    else:
        logging.info(f"No scaling required. Current node count ({current_node_count}) is sufficient.")
        return current_node_count

def main():
    try:
        logging.info(f"Starting gameplay warm pool manager with USE_EC2_COUNT={USE_EC2_COUNT}")
        while True:
            # Get current node count based on configuration
            total_nodes, running_nodes, pending_nodes = get_current_node_count()
            pod_count = get_pod_count()
            logging.info(f"Found {pod_count} pods in namespace {NAMESPACE}")

            new_desired_size = calculate_desired_size(pod_count, total_nodes)

            # Only scale up, never scale down
            if new_desired_size > total_nodes:
                update_eks_nodegroup(new_desired_size)
                logging.info("================================================================")
                logging.info("\nWait for 60 Sec...")
                logging.info("================================================================")
                time.sleep(60)

            time.sleep(SLEEP_INTERVAL)
    except KeyboardInterrupt:
        logging.info("\nScript interrupted. Exiting gracefully...")

if __name__ == "__main__":
    main()