export interface RegistryPreset {
  id: string;
  name: string;
  host: string;
  logo: string;
}

export const KNOWN_REGISTRY_PRESETS: RegistryPreset[] = [
  { id: "dockerhub", name: "Docker Hub", host: "docker.io", logo: "🐳" },
  { id: "ghcr", name: "GitHub Container Registry", host: "ghcr.io", logo: "🐙" },
  { id: "quay", name: "Quay", host: "quay.io", logo: "🟥" },
  { id: "gitlab", name: "GitLab Registry", host: "registry.gitlab.com", logo: "🦊" },
  { id: "aws-ecr", name: "Amazon ECR", host: "<account>.dkr.ecr.<region>.amazonaws.com", logo: "☁️" },
  { id: "azure-acr", name: "Azure ACR", host: "<registry>.azurecr.io", logo: "🔷" },
  { id: "gcp-gar", name: "Google Artifact Registry", host: "<region>-docker.pkg.dev", logo: "🟡" },
];
