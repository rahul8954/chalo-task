# PostgreSQL Infrastructure Automation

This repository automates the deployment and configuration of a PostgreSQL cluster on AWS using Terraform and Ansible. It enables seamless scaling and replication management.

## Overview
This project automates the deployment of PostgreSQL infrastructure on AWS, utilizing Docker, Terraform, and Ansible. It provisions primary and replica PostgreSQL servers and provides a REST API for interacting with the infrastructure.

## API Documentation

### 1. `/generate`
**Method:** `POST`  
**Description:** Generates Terraform configuration files for the PostgreSQL infrastructure. It takes user input for the instance type and the number of replica instances, writing the generated configurations to the `terraform_configs` directory.

#### Parameters:
- `instance_type`: *(optional)* The type of EC2 instance for PostgreSQL servers. Default is `t3.medium`.
- `num_replicas`: *(optional)* The number of replica PostgreSQL instances. Default is `2`.

#### Example Request Body:
```json
{
  "instance_type": "t3.large",
  "num_replicas": 3
}
```

### 2. `/apply`
**Method:** `POST`
**Description:** Applies the generated Terraform configurations, provisioning the infrastructure on AWS.

#### Parameters:
{}

### 3. `/apply_ansible_configuration`
**Method:** `POST`
**Description:**
Generates the Ansible inventory file and playbook based on the Terraform outputs. It also updates `docker-compose.yml` and `postgresql.conf` to configure PostgreSQL containers with user-defined settings.

- Ansible inventory file is generated using the Terraform output (primary and replica instance IP addresses).
- `docker-compose.yml` and `postgresql.conf` are updated with user-defined values for the Docker image, max connections, and shared buffers.

#### Parameters:
- `image_tag`: *(optional)* The Docker image tag for PostgreSQL. Default is `postgres:14-alpine`.
- `shared_buffers`: *(optional)* Shared buffers size for PostgreSQL. Default is `128MB`.
- `max_connection`: *(optional)* Maximum number of PostgreSQL connections. Default is `200`.

#### Example Request Body:
```json
{
  "image_tag": "latest",
  "max_connection": "200",
  "shared_buffers": "256MB"
}
```

## Docker Compose Configuration
The `docker-compose.yml` file defines the PostgreSQL primary and replica servers.

### Key Configurations:
- **Image**: `postgres:{{ postgres_image_tag }}`
- **Authentication**: `scram-sha-256` with `md5` for replication.
- **Replication Settings**:
  - `wal_level=replica`
  - `hot_standby=on`
  - `max_wal_senders=10`
  - `max_replication_slots=10`
  - `hot_standby_feedback=on`
- **Performance Tuning**:
  - `shared_buffers={{ postgres_shared_buffers }}`
  - `max_connections={{ postgres_max_connection }}`
- **Volumes**:
  - `./00_init.sql:/docker-entrypoint-initdb.d/00_init.sql`

## Running the Application

To run the PostgreSQL container:
```sh
docker-compose up -d
```

To access the running PostgreSQL container:
```sh
docker exec -it POSTGRES_CONTAINER_ID /bin/bash
```
Verify PostgreSQL running 
```
psql -U user -d postgress 

SELECT
    application_name,
    client_addr,
    state,
    now() - pg_last_xact_replay_timestamp() AS replication_lag_time
FROM
    pg_stat_replication;
```
## Cleanup
To remove the infrastructure:
```sh
terraform destroy
```

