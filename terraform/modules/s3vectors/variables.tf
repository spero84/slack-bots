variable "vector_bucket_name" {
  description = "Name of the S3 Vectors bucket"
  type        = string
}

variable "force_destroy" {
  description = "Force destroy all indexes when deleting the bucket"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}
