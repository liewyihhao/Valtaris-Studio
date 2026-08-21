import type { TipsCollection } from "./types";

// Valtaris Studio — neutral, product-accurate tips (no third-party upsell),
// paired with the live-fetch disabled in ./utils so only these are shown.
export const defaultTipsCollection: TipsCollection = {
  projectCreation: [
    {
      title: "Did you know?",
      content:
        "Use or modify dozens of pre-built templates to configure your labeling UI, or build a custom configuration from scratch with simple XML-like tags.",
      closable: true,
      link: { label: "Learn more", url: "https://labelstud.io/guide/setup" },
    },
    {
      title: "Labeling for GenAI",
      content:
        "Templates are available for supervised LLM fine-tuning, RAG retrieval ranking, RLHF, chatbot evaluation, and more.",
      closable: true,
      link: { label: "Explore templates", url: "https://labelstud.io/templates/gallery_generative_ai" },
    },
    {
      title: "Did you know?",
      content:
        "You can import tasks from JSON, CSV, or TSV, or sync directly from cloud storage to keep new data flowing into your projects.",
      closable: true,
      link: { label: "Learn more", url: "https://labelstud.io/guide/tasks" },
    },
  ],
  organizationPage: [
    {
      title: "Did you know?",
      content:
        "Valtaris Studio integrates with all popular cloud storage providers, machine learning models, and common tools to automate your data pipeline.",
      closable: true,
      link: { label: "Browse integrations", url: "https://labelstud.io/integrations/" },
    },
    {
      title: "Connect your models",
      content:
        "Attach a machine learning backend to pre-label data and speed up annotation with model predictions and active learning.",
      closable: true,
      link: { label: "Learn more", url: "https://labelstud.io/guide/ml" },
    },
  ],
  projectSettings: [
    {
      title: "Did you know?",
      content: "You can connect ML models using the backend SDK to save time with pre-labeling or active learning.",
      closable: true,
      link: { label: "Learn more", url: "https://labelstud.io/guide/ml" },
    },
    {
      title: "Export in the format you need",
      content:
        "Export annotations as JSON, CSV, COCO, YOLO, VOC, and more — ready to train and evaluate your models.",
      closable: true,
      link: { label: "Learn more", url: "https://labelstud.io/guide/export" },
    },
  ],
};
