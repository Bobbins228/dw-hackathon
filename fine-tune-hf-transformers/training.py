from datasets import load_dataset
from transformers import AutoTokenizer
import ray.data
import numpy as np
from typing import Dict
import torch
import numpy as np

from datasets import load_metric
from transformers import AutoModelForSequenceClassification, TrainingArguments, Trainer

import ray.train
from ray.train.huggingface.transformers import prepare_trainer, RayTrainReportCallback
from ray.train.torch import TorchTrainer
from ray.train import RunConfig, ScalingConfig, CheckpointConfig
import os
storage_path=os.environ.get("AWS_BUCKET_URI")  # S3 Storage

use_gpu = True  # set this to False to run on CPUs
num_workers = 1  # set this to number of GPUs or CPUs you want to use

GLUE_TASKS = [
    "cola",
    "mnli",
    "mnli-mm",
    "mrpc",
    "qnli",
    "qqp",
    "rte",
    "sst2",
    "stsb",
    "wnli",
]

task = "cola"
model_checkpoint = "distilbert-base-uncased"
batch_size = 16

actual_task = "mnli" if task == "mnli-mm" else task
datasets = load_dataset("glue", actual_task, trust_remote_code=True)

tokenizer = AutoTokenizer.from_pretrained(model_checkpoint, use_fast=True,trust_remote_code=True)

task_to_keys = {
    "cola": ("sentence", None),
    "mnli": ("premise", "hypothesis"),
    "mnli-mm": ("premise", "hypothesis"),
    "mrpc": ("sentence1", "sentence2"),
    "qnli": ("question", "sentence"),
    "qqp": ("question1", "question2"),
    "rte": ("sentence1", "sentence2"),
    "sst2": ("sentence", None),
    "stsb": ("sentence1", "sentence2"),
    "wnli": ("sentence1", "sentence2"),
}



ray_datasets = {
    "train": ray.data.from_huggingface(datasets["train"]),
    "validation": ray.data.from_huggingface(datasets["validation"]),
    "test": ray.data.from_huggingface(datasets["test"]),
}
ray_datasets

import pkg_resources

def print_package_versions(packages):
    for package in packages:
        try:
            version = pkg_resources.get_distribution(package).version
            print(f"{package}: {version}")
        except pkg_resources.DistributionNotFound:
            print(f"{package}: Package not found")

# Example usage:
packages = ['numpy', 'ray', 'transformers', 'torch', 'mlflow', 'accelerate']
print_package_versions(packages)

# Tokenize input sentences
def collate_fn(examples: Dict[str, np.array]):
    sentence1_key, sentence2_key = task_to_keys[task]
    if sentence2_key is None:
        outputs = tokenizer(
            list(examples[sentence1_key]),
            truncation=True,
            padding="longest",
            return_tensors="pt",
        )
    else:
        outputs = tokenizer(
            list(examples[sentence1_key]),
            list(examples[sentence2_key]),
            truncation=True,
            padding="longest",
            return_tensors="pt",
        )

    outputs["labels"] = torch.LongTensor(examples["label"])

    # Move all input tensors to GPU
    for key, value in outputs.items():
        outputs[key] = value.cuda()

    return outputs

num_labels = 3 if task.startswith("mnli") else 1 if task == "stsb" else 2
metric_name = (
    "pearson"
    if task == "stsb"
    else "matthews_correlation"
    if task == "cola"
    else "accuracy"
)
model_name = model_checkpoint.split("/")[-1]
validation_key = (
    "validation_mismatched"
    if task == "mnli-mm"
    else "validation_matched"
    if task == "mnli"
    else "validation"
)
name = f"{model_name}-finetuned-{task}"

# Calculate the maximum steps per epoch based on the number of rows in the training dataset.
# Make sure to scale by the total number of training workers and the per device batch size.
max_steps_per_epoch = ray_datasets["train"].count() // (batch_size * num_workers)


def train_func(config):
    print(f"Is CUDA available: {torch.cuda.is_available()}")

    metric = load_metric("glue", actual_task, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(model_checkpoint, use_fast=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_checkpoint, num_labels=num_labels
    )

    train_ds = ray.train.get_dataset_shard("train")
    eval_ds = ray.train.get_dataset_shard("eval")

    train_ds_iterable = train_ds.iter_torch_batches(
        batch_size=batch_size, collate_fn=collate_fn
    )
    eval_ds_iterable = eval_ds.iter_torch_batches(
        batch_size=batch_size, collate_fn=collate_fn
    )

    print("max_steps_per_epoch: ", max_steps_per_epoch)

    args = TrainingArguments(
        name,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        logging_strategy="epoch",
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=config.get("learning_rate", 2e-5),
        num_train_epochs=config.get("epochs", 2),
        weight_decay=config.get("weight_decay", 0.01),
        push_to_hub=False,
        max_steps=max_steps_per_epoch * config.get("epochs", 2),
        disable_tqdm=True,  # declutter the output a little
        no_cuda=not use_gpu,  # you need to explicitly set no_cuda if you want CPUs
        report_to="none",
    )

    def compute_metrics(eval_pred):
        predictions, labels = eval_pred
        if task != "stsb":
            predictions = np.argmax(predictions, axis=1)
        else:
            predictions = predictions[:, 0]
        return metric.compute(predictions=predictions, references=labels)

    trainer = Trainer(
        model,
        args,
        train_dataset=train_ds_iterable,
        eval_dataset=eval_ds_iterable,
        tokenizer=tokenizer,
        compute_metrics=compute_metrics,
    )

    trainer.add_callback(RayTrainReportCallback())

    trainer = prepare_trainer(trainer)

    print("Starting training")
    trainer.train()

trainer = TorchTrainer(
    train_func,
    scaling_config=ScalingConfig(num_workers=num_workers, use_gpu=use_gpu),
    datasets={
        "train": ray_datasets["train"],
        "eval": ray_datasets["validation"],
    },
    run_config=RunConfig(
        storage_path=storage_path , name="example-lightning",
        checkpoint_config=CheckpointConfig(
            num_to_keep=1,
            checkpoint_score_attribute="eval_loss",
            checkpoint_score_order="min",
        ),
    ),
)

result = trainer.fit()