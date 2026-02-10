# Terraform Infrastructure for LUGA Project

이 프로젝트는 AWS CDK에서 Terraform/Terragrunt로 변환된 인프라 코드입니다.

## 디렉토리 구조

```
terraform/
├── modules/              # 재사용 가능한 Terraform 모듈
│   ├── s3/              # S3 버킷 (Access Log, Data)
│   ├── vpc/             # VPC, 서브넷, 라우트 테이블, VPC 엔드포인트
│   ├── iam/             # IAM 역할 및 정책
│   ├── ecr/             # ECR 리포지토리
│   ├── ecs/             # ECS 클러스터 및 CloudWatch 로그 그룹
│   ├── dynamodb/        # DynamoDB 테이블
│   ├── cognito/         # Cognito User Pool 및 Client
│   └── cicd/            # CodeCommit, CodeBuild, CodePipeline
└── environments/        # 환경별 Terragrunt 구성
    ├── terragrunt.hcl   # 루트 Terragrunt 구성
    └── poc/             # POC 환경
        ├── env.hcl      # 환경별 변수
        └── ...          # 각 모듈별 Terragrunt 구성
```

## 전제 조건

1. **Terraform** >= 1.5.0
2. **Terragrunt** >= 0.50.0
3. **AWS CLI** 구성 완료
4. **S3 버킷 및 DynamoDB 테이블** (Terraform 상태 저장용)

## 초기 설정

### 1. Terraform 백엔드 설정

먼저 Terraform 상태를 저장할 S3 버킷과 잠금용 DynamoDB 테이블을 생성해야 합니다:

```bash
# S3 버킷 생성
aws s3api create-bucket \
    --bucket terraform-state-poc-ap-northeast-2 \
    --region ap-northeast-2 \
    --create-bucket-configuration LocationConstraint=ap-northeast-2

# 버킷 버저닝 활성화
aws s3api put-bucket-versioning \
    --bucket terraform-state-poc-ap-northeast-2 \
    --versioning-configuration Status=Enabled

# DynamoDB 테이블 생성 (잠금용)
aws dynamodb create-table \
    --table-name terraform-locks-poc \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5 \
    --region ap-northeast-2
```

### 2. 환경 변수 설정

POC 환경의 변수는 `environments/poc/env.hcl` 파일에서 관리됩니다. 필요에 따라 수정하세요:

```hcl
locals {
  environment = "poc"
  project_name = "luga-poc"  # 프로젝트 이름 변경 가능
  region = "ap-northeast-2"
  vpc_cidr = "10.0.0.0/24"
  # ... 기타 설정
}
```

## 배포

### 전체 인프라 배포

POC 환경의 모든 리소스를 한번에 배포:

```bash
cd environments/poc
terragrunt run-all apply
```

### 개별 모듈 배포

특정 모듈만 배포하려면:

```bash
# VPC 모듈만 배포
cd environments/poc/vpc
terragrunt apply

# IAM 모듈만 배포
cd environments/poc/iam
terragrunt apply
```

### 권장 배포 순서

의존성을 고려한 권장 배포 순서:

1. S3
2. VPC
3. IAM
4. ECR
5. ECS
6. DynamoDB
7. Cognito
8. CICD

```bash
cd environments/poc

# 순차적 배포
terragrunt apply --terragrunt-working-dir s3
terragrunt apply --terragrunt-working-dir vpc
terragrunt apply --terragrunt-working-dir iam
terragrunt apply --terragrunt-working-dir ecr
terragrunt apply --terragrunt-working-dir ecs
terragrunt apply --terragrunt-working-dir dynamodb
terragrunt apply --terragrunt-working-dir cognito
terragrunt apply --terragrunt-working-dir cicd
```

## 인프라 확인

배포 후 리소스 확인:

```bash
# 전체 상태 확인
cd environments/poc
terragrunt run-all output

# 특정 모듈 상태 확인
cd environments/poc/vpc
terragrunt output
```

## 인프라 삭제

### 전체 인프라 삭제

```bash
cd environments/poc
terragrunt run-all destroy
```

### 개별 모듈 삭제

```bash
cd environments/poc/cicd
terragrunt destroy
```

## 모듈 설명

### S3 모듈
- Access Log 버킷: 액세스 로그 저장용
- Data 버킷: 인덱스 데이터 저장용
- 모든 버킷은 암호화, SSL 전용, 퍼블릭 액세스 차단 설정

### VPC 모듈
- CIDR: 10.0.0.0/24
- 가용 영역: ap-northeast-2a, ap-northeast-2c
- 서브넷 타입: Private Isolated (ALB, App, TGW)
- VPC 엔드포인트: ECR, ECS, CloudWatch, Bedrock 등
- 보안 그룹: Endpoint SG, ECS Task SG

### IAM 모듈
- ECS Task Role: Bedrock, DynamoDB, S3 접근 권한
- ECS Task Execution Role: ECR, CloudWatch Logs 접근 권한
- CodeBuild Role: ECR, CloudWatch Logs 접근 권한
- CodeDeploy Role: ECS 배포 권한
- CodePipeline Role: 파이프라인 실행 권한

### ECR 모듈
- UI 리포지토리: React/TypeScript 애플리케이션용
- API 리포지토리: Python 애플리케이션용

### ECS 모듈
- ECS 클러스터
- CloudWatch 로그 그룹 (7일 보관)

### DynamoDB 모듈
- Conversation Table: 대화 데이터 저장 (GSI 2개, LSI 1개)
- Connection Table: WebSocket 연결 관리 (TTL 활성화)
- Admin Table: 관리자 데이터 (TTL 활성화)

### Cognito 모듈
- User Pool: 이메일 기반 인증
- User Pool Client: OAuth 2.0 지원
- Cognito Domain: 호스팅 UI용

### CICD 모듈
- CodeCommit: API, UI 소스 코드 저장소
- CodeBuild: Docker 이미지 빌드
- CodePipeline: CI/CD 파이프라인

## 주의사항

1. **프로젝트 이름 변경**: `env.hcl`의 `project_name`을 변경하면 모든 리소스 이름이 변경됩니다.

2. **VPC CIDR 변경**: 기본값은 `10.0.0.0/24`입니다. 변경 시 서브넷 CIDR도 함께 확인하세요.

3. **Cognito Domain**: 전역적으로 고유해야 합니다. 충돌 시 `cognito_domain_prefix`를 변경하세요.

4. **리소스 삭제**: S3 버킷에 객체가 있으면 삭제가 실패할 수 있습니다. 먼저 버킷을 비우세요.

## 다른 환경 추가

새로운 환경(예: dev, staging, prod)을 추가하려면:

1. 환경 디렉토리 생성:
```bash
cp -r environments/poc environments/dev
```

2. `environments/dev/env.hcl` 수정:
```hcl
locals {
  environment = "dev"
  project_name = "luga-dev"
  # ... 기타 환경별 설정
}
```

3. 백엔드 리소스 생성 (S3 버킷, DynamoDB 테이블)

4. 배포:
```bash
cd environments/dev
terragrunt run-all apply
```

## 문제 해결

### Terragrunt 캐시 문제
```bash
# 캐시 삭제
find . -type d -name ".terragrunt-cache" -exec rm -rf {} +
```

### 의존성 문제
```bash
# 의존성 그래프 확인
cd environments/poc
terragrunt graph-dependencies
```

### 상태 잠금 문제
```bash
# 잠금 강제 해제 (주의: 다른 프로세스가 없는지 확인)
terraform force-unlock <LOCK_ID>
```

## CDK와의 차이점

1. **명시적 리소스 생성**: CDK가 자동으로 생성하는 리소스(IAM 역할, 보안 그룹 등)를 Terraform에서는 명시적으로 정의

2. **모듈화**: 재사용 가능한 모듈로 분리하여 다중 환경 관리 용이

3. **상태 관리**: Terraform은 상태 파일을 통해 리소스 추적 (S3 백엔드 사용)

4. **변수 관리**: Terragrunt를 통한 DRY 원칙 적용 및 환경별 변수 분리

## 지원 및 문의

프로젝트 관련 문의사항이 있으시면 이슈를 등록해주세요.