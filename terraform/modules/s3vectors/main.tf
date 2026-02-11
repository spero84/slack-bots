resource "aws_s3vectors_vector_bucket" "this" {
  vector_bucket_name = var.vector_bucket_name
  force_destroy      = var.force_destroy

  tags = var.tags
}
