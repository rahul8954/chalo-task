from flask import Flask, request, jsonify
import os
import subprocess
import json

app = Flask(__name__)

# Paths for generated files
TERRAFORM_DIR = "terraform_configs"
ANSIBLE_DIR = "ansible_configs"
@app.route("/generate_terraform_configuration", methods=["POST"])
def generate_configs():
    """
    Generate Terraform configurations based on input parameters.
    """
    data = request.json
    
    # Extract parameters
    instance_type = data.get("instance_type", "t3.medium")
    num_replicas = data.get("num_replicas", 2)

    if not os.path.exists(TERRAFORM_DIR):
        os.makedirs(TERRAFORM_DIR)

    # Generate Terraform code
    terraform_code = f"""
    provider "aws" {{
      region = "ap-northeast-2"
    }}

    resource "aws_key_pair" "ansible_key" {{
      key_name   = "ansible-key"
      public_key = file("/root/.ssh/id_rsa.pub")
    }}

    resource "aws_instance" "postgres_primary" {{
      ami           = "ami-0dc44556af6f78a7b"
      instance_type = "{instance_type}"
      key_name      = aws_key_pair.ansible_key.key_name
      user_data = <<-EOF
              #!/bin/bash
              curl -fsSL https://get.docker.com -o get-docker.sh
              sh get-docker.sh
              EOF
      tags = {{
        Name = "PostgresPrimary"
      }}
    }}

    resource "aws_instance" "postgres_replicas" {{
      count         = {num_replicas}
      ami           = "ami-0dc44556af6f78a7b"
      instance_type = "{instance_type}"
      key_name      = aws_key_pair.ansible_key.key_name
      user_data = <<-EOF
              #!/bin/bash
              curl -fsSL https://get.docker.com -o get-docker.sh
              sh get-docker.sh
              EOF
      tags = {{
        Name = "PostgresReplica${{count.index + 1}}"
      }}
    }}

    output "primary_instance_public_ip" {{
      value = aws_instance.postgres_primary.public_ip
    }}

    output "replica_instance_public_ips" {{
      value = [for instance in aws_instance.postgres_replicas : instance.public_ip]
    }}

    output "primary_instance_private_ip" {{
      value = aws_instance.postgres_primary.private_ip
    }}
    """

    with open(os.path.join(TERRAFORM_DIR, "main.tf"), "w") as tf_file:
        tf_file.write(terraform_code)

    return jsonify({"message": "Terraform configurations generated successfully."})


@app.route("/apply_terraform_configuration", methods=["POST"])
def apply_terraform():
    """
    Run Terraform plan and apply.
    """
    try:
        os.chdir(TERRAFORM_DIR)
        subprocess.check_output(["terraform", "init"])
        subprocess.check_output(["terraform", "apply", "--auto-approve"])
        os.chdir("../")
        return jsonify({"message": "Infrastructure created successfully."})
    except subprocess.CalledProcessError as e:
        os.chdir("../")
        return jsonify({"error": f"Terraform error: {e.output.decode()}"}), 500

@app.route("/apply_ansible_setup", methods=["POST"])
def apply_ansible_configuration():
    """
    Generate Ansible inventory file based on Terraform outputs.
    """
    data = request.json
    postgres_image_tag = data.get("image_tag", "postgres:14-alpine")
    postgres_max_connection=data.get("max_connection","200")
    postgres_shared_buffers= data.get("shared_buffers","128MB")
    try:
        # Fetch Terraform output
        os.chdir(TERRAFORM_DIR)
        terraform_output = subprocess.check_output(["terraform", "output", "-json"])
        output_data = json.loads(terraform_output)
        os.chdir("../")

        # Extract IPs
        primary_ip = output_data["primary_instance_public_ip"]["value"]
        replica_ips = output_data["replica_instance_public_ips"]["value"]
        primary_instance_private_ip = output_data["primary_instance_private_ip"]["value"]

        # Ensure Ansible directory exists
        if not os.path.exists(ANSIBLE_DIR):
            os.makedirs(ANSIBLE_DIR)

        # Generate inventory file
        inventory_file_path = os.path.join(ANSIBLE_DIR, "inventory.txt")
        with open(inventory_file_path, "w") as inventory_file:
            inventory_file.write("[all:vars]\n")
            inventory_file.write(f"postgres_image_tag={postgres_image_tag}\n")
            inventory_file.write(f"postgres_max_connection={postgres_max_connection}\n")
            inventory_file.write(f"postgres_shared_buffers={postgres_shared_buffers}\n\n")
            inventory_file.write("[primary]\n")
            inventory_file.write(f"{primary_ip} ansible_user=ubuntu ansible_ssh_private_key_file=/root/.ssh/id_rsa\n\n")
            inventory_file.write("[replicas]\n")
            for ip in replica_ips:
                inventory_file.write(f"{ip} ansible_user=ubuntu ansible_ssh_private_key_file=/root/.ssh/id_rsa\n")

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Error fetching Terraform output: {e.output.decode()}"}), 500
    except KeyError as e:
        return jsonify({"error": f"Missing expected output key: {str(e)}"}), 500
    

    # Define paths for primary and replica docker-compose.yml
    primary_source_path = '/app/master/docker-compose.yml.j2'
    replica_source_path = '/app/slave/docker-compose.yml.j2'
    common_dest_path = '/tmp/'  # Common destination path
    primary_sql_script_path='/app/master/00_init.sql'
    sql_script="create user replicator with replication encrypted password 'replicator_password';\n"

    try:
        with open("/app/slave/docker-compose.yml.j2", "r") as file:
            file_content = file.read()
        updated_content = file_content.replace("DEVOPS_PRIMARY_HOST", primary_instance_private_ip)
        with open("/app/slave/docker-compose.yml.j2", "w") as file:
            file.write(updated_content)

    except Exception as e:
        return jsonify({"error": f"Something went wrong while modifying replica configurations: {str(e)}"}), 500


    for ip in replica_ips:
        ip = ip.replace(".", "_")
        replication_slot = f"select pg_create_physical_replication_slot('replication_slot_{ip}');\n"
        sql_script=sql_script + replication_slot
    with open("/app/master/00_init.sql","w") as file:
        file.write(sql_script)


    # Ansible playbook structure
    playbook = """
---
- name: Deploy Docker Compose for Primary
  hosts: primary
  become: true
  tasks:
    - name: Copy docker-compose file for primary
      template:
        src: {primary_source}
        dest: {common_dest}

    - name: Copy sql script
      copy:
        src: {primary_sql_script_source}
        dest: {common_dest}

    - name: Change directory to /tmp and run docker compose up -d for primary
      shell: |
        cd /tmp
        sudo chmod -R 777 /tmp/
        sudo docker compose -f docker-compose.yml.j2 up -d
      args:
        chdir: /tmp/

- name: Deploy Docker Compose for Replicas
  hosts: replicas
  become: true
  tasks:
    - name: Copy docker-compose file for replica
      template:
        src: {replica_source}
        dest: {common_dest}

    - name: Change directory to /tmp/ and run docker compose up -d for replica
      shell: |
        cd /tmp/
        sudo chmod -R 777 /tmp/
        sudo docker compose -f docker-compose.yml.j2 up -d
      args:
        chdir: /tmp/
""".format(
        primary_source=primary_source_path,
        replica_source=replica_source_path,
        common_dest=common_dest_path,
        primary_sql_script_source=primary_sql_script_path
    )

    # Save the generated playbook
    playbook_file_path = os.path.join(ANSIBLE_DIR, "playbook.yml")
    try:
        with open(playbook_file_path, 'w') as f:
            f.write(playbook)
    except Exception as e:
        return jsonify({"error": f"Failed to generate Ansible playbook: {str(e)}"}), 500
    
    ## Run the ansible playbook generated
    try:
        os.chdir(ANSIBLE_DIR)
        subprocess.check_output(["ansible-playbook", "-i", "inventory.txt", "playbook.yml", "--ssh-extra-args=-o StrictHostKeyChecking=no"])
        os.chdir("../")
        return jsonify({"message": "Applied ansible playbook successfully postgres is now enabled in replication mode"})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Ansible error: {e.output.decode()}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
