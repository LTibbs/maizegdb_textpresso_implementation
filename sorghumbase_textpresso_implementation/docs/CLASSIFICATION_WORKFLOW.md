# Sorghum Literature PDF Classification Workflow

## Tools and Libraries Used

### Core Python Libraries
- **scikit-learn**: Machine learning framework for model training, evaluation, and feature selection (Logistic Regression classifier used in this workflow).
- **nltk**: Natural language processing toolkit for tokenization and lemmatization.
- **PyPDF2**: PDF parsing and text extraction.
- **pandas**: Data manipulation and CSV handling.
- **joblib**: Model serialization and persistence.
- **numpy, scipy**: Scientific computing and data processing.
- **requests**: HTTP requests for PubMed/PMC API access (used in harvester script).

### Textpresso Classifiers Module
- **textpresso_classifiers.classifiers**: Provides the `TextpressoDocumentClassifier` class, feature extraction (TF-IDF, CountVectorizer), lemmatization, n-gram extraction, and chi-squared feature selection.
- **Supported Classifiers** (from the library, though this workflow uses Logistic Regression):
  - SVM (linear/non-linear)
  - Linear Discriminant Analysis
  - Gaussian Process
  - Naive Bayes
  - XGBoost
  - Decision Tree
  - Random Forest
  - K-Nearest Neighbors
  - Multi-Layer Perceptrons (Neural Network)
  - Radial Basis Function Neural Network
- **Feature Engineering**: Lemmatization, n-gram extraction, stopword removal, chi-squared feature selection.

### Example Used in This Workflow
- **Classifier Used**: Logistic Regression (from scikit-learn)
- **Feature Extraction**: TF-IDF, n-grams, optional lemmatization
- **Feature Selection**: Chi-squared (from scikit-learn)

---

## Overview
This project provides an end-to-end workflow for harvesting, labeling, training, and classifying sorghum research PDFs using machine learning. The workflow leverages PubMed/PMC APIs, robust PDF-to-text conversion, manual labeling, and a scikit-learn-based classifier for document categorization.

---

## Workflow Steps

### 1. Harvesting PDFs
- **Script:** `scripts/sorghum_pdf_harvester.py`
- **Purpose:** Downloads PDFs for sorghum literature using PubMed IDs and PMCIDs.
- **Example Usage:**
  ```bash
  python sorghumbase_textpresso_implementation/scripts/sorghum_pdf_harvester.py --query "sorghum" --max-papers 50 --output-dir harvested_pdfs
  ```
- **Result:** PDFs are saved in the specified output directory.

### 2. Converting PDFs to Text & Creating Labels Template
- **Script:** `scripts/pdf_to_text_and_labels.py`
- **Purpose:** Converts harvested PDFs to plain text and generates a CSV template for manual labeling.
- **Example Usage:**
  ```bash
  python sorghumbase_textpresso_implementation/scripts/pdf_to_text_and_labels.py --pdf-dir harvested_pdfs --output-csv training_data.csv
  ```
- **Result:**
  - Extracted text files for each PDF.
  - `training_data.csv` with columns: `filename`, `text`, `label` (to be filled manually).

### 3. Manual Labeling
- **Action:** Open `training_data.csv` and assign a category label to each document under the `label` column.
- **Categories Example:**
  - MOLECULAR_GENETICS
  - FIELD_PHYSIOLOGY
  - MODELING_REMOTE_SENSING
  - OTHER

### 4. Training the Classifier
- **Script:** `scripts/train_and_eval_classifier.py`
- **Purpose:** Trains a document classifier using the labeled CSV and evaluates its performance.
- **Example Usage:**
  ```bash
  python sorghumbase_textpresso_implementation/scripts/train_and_eval_classifier.py --input-csv training_data.csv --model-out classifier.joblib
  ```
- **Result:**
  - Trained model saved as `classifier.joblib`.
  - Evaluation metrics (accuracy, confusion matrix) printed to console.

### 5. Predicting on New PDFs
- **Script:** `scripts/predict_new_pdfs.py`
- **Purpose:** Converts new PDFs to text and predicts their categories using the trained model.
- **Example Usage:**
  ```bash
  python sorghumbase_textpresso_implementation/scripts/predict_new_pdfs.py --pdf-dir new_pdfs --model classifier.joblib --output-csv predictions.csv
  ```
- **Result:**
  - `predictions.csv` with columns: `filename`, `predicted_label`.

---

## Example Results

### Classifier Training Output
```
Loaded 20 documents for training.
Training classifier...
Accuracy: 0.85
Confusion Matrix:
[[5 0 0 0]
 [0 4 1 0]
 [0 1 4 0]
 [0 0 0 5]]
```

### Prediction Output
```
filename,predicted_label
PMC1234567.pdf,MOLECULAR_GENETICS
PMC2345678.pdf,FIELD_PHYSIOLOGY
PMC3456789.pdf,OTHER
```


## Sorghum Run: Example Data and Outputs

### Labeled Data (labels.tsv)
| doc_id | label | explanation |
|--------|---------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1-s2.0-S0168945225005746-main | MOLECULAR_GENETICS | Genome-wide identification and functional characterization of SbHMA5, a Cu-transporting P1B-type ATPase, including yeast complementation, localization and interaction with metallochaperones. |
| fpls-16-1682270 | MOLECULAR_GENETICS | GWAS on 245 sorghum accessions for salt tolerance at germination/seedling stage; maps 35 loci and 39 candidate genes controlling salt-response traits. |
| s41598-025-29176-y | MOLECULAR_GENETICS | Genomic selection study for Fe and Zn biofortification using ST-GBLUP vs MT-GBLUP in sorghum minicore; focuses on GS models and genetic prediction of micronutrient traits. |
| pcaf160 | MOLECULAR_GENETICS | Characterization of Sorghum bicolor membrane steroid binding protein 1 (SbMSBP1); phylogenetic analysis, heme binding, ER remodeling, and stress resilience functions. |
| Plant Cell Environment - 2025 - Pilloni - Large Variations in the Transpiration of Sorghum Canopies Under High | FIELD_PHYSIOLOGY | Field study of 47 sorghum genotypes; analyzes canopy transpiration, water use efficiency, and genetic variability under high evaporative demand and VPD. |

### Label Template (labels_template.tsv)
This file provides a template for manual labeling of new documents:
| doc_id | label |
|--------|-------|
| 1-s2.0-S0168945225005746-main |       |
| Plant Cell Environment - 2025 - Pilloni - Large Variations in the Transpiration of Sorghum Canopies Under High |       |
| fpls-16-1682270 |       |
| pcaf160 |       |
| s41598-025-29176-y |       |

### Extracted Text Files
Text files for each document are available in `tmp_txt/` and `txt/` subfolders. These contain the full text extracted from each PDF, ready for feature extraction and classification.


### Model File: sorghum_classifier.pkl
The `sorghum_classifier.pkl` file is a serialized (pickled) machine learning model generated during **Step 4: Training the Classifier**. This file is created by the `scripts/train_and_eval_classifier.py` script using scikit-learn's joblib or pickle. It contains the trained classifier, including its learned parameters and feature mappings, allowing you to load and use the model for prediction on new data without retraining. This enables reproducible and efficient classification workflows.

**Generation Step:**
  - Step 4: Training the Classifier
  - Script: `scripts/train_and_eval_classifier.py`
  - Command Example:
    ```bash
    python sorghumbase_textpresso_implementation/scripts/train_and_eval_classifier.py --input-csv training_data.csv --model-out sorghum_classifier.pkl
    ```
  - Output: `sorghum_classifier.pkl` (saved model)

**Usage:**
  - Step 5: Predicting on New PDFs
  - Script: `scripts/predict_new_pdfs.py`
  - The model is loaded for making predictions on new documents.

---

## Usage Guide

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Harvest PDFs:** Use the harvester script with your query.
3. **Convert PDFs to text and create a labels template.**
4. **Manually label the documents in the CSV.**
5. **Train the classifier.**
6. **Predict categories for new PDFs.**

---

## Notes & Best Practices
- Ensure a balanced labeled dataset for best classifier performance.
- Review extracted text for quality; some PDFs may not convert cleanly.
- Expand the labeled set as new categories or documents are encountered.
- All scripts are robust to missing/corrupt PDFs and will log errors.

---

## References
- [PubMed API Documentation](https://www.ncbi.nlm.nih.gov/books/NBK25501/)
- [scikit-learn Documentation](https://scikit-learn.org/stable/)
- [PyPDF2 Documentation](https://pypdf2.readthedocs.io/en/latest/)

---

## Contact
For questions or contributions, see the project README or contact the maintainer.
