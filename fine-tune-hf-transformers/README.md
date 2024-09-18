# Fine-tune a Hugging Face Transformers Model

This training script is based on the example of the same name from the [Ray guided examples](https://docs.ray.io/en/latest/train/examples/transformers/huggingface_text_classification.html).
The following is a list of information about the environment used to successfully run this example.
* OpenShift version: v4.14
* OpenShift AI version: v2.13
* CodeFlare SDK: v0.19.1
* Ray Image: `quay.io/rhoai/ray:2.23.0-py39-cu121`
* 4 g5.4xlarge instances

Ray Cluster Configurations:
```
num_cpu=10
num_memory=55

cluster = Cluster(ClusterConfiguration(
    name='raytest',
    head_extended_resource_requests={'nvidia.com/gpu':1},
    worker_extended_resource_requests={'nvidia.com/gpu':1},
    num_workers=3,
    head_cpus=num_cpu,
    head_memory=num_memory,
    worker_cpu_requests=num_cpu,
    worker_cpu_limits=num_cpu,
    worker_memory_requests=num_memory,
    worker_memory_limits=num_memory,
    write_to_file=True,
))
```