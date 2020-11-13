import random
from collections import Counter
import os
import pandas as pd
import argparse
import file as fi


def split_heart(input_path='../heart', split_dir='../heart/splits', seed=252,
                distribution_dict=None):
    """create split files"""
    if distribution_dict is None or not distribution_dict:
        distribution_dict = {1: 350, -1: 350}
    if seed:
        random.seed(seed)
    # load diagnosis cv and extract unqiue diagnoses
    diag_csv = os.path.join(input_path, 'heart_labels.csv')
    diagnosis = pd.read_csv(diag_csv)
    ds = list(diagnosis['label'].unique())
    pIdbydiagnosis = {}
    train_list = []
    for diag in ds:
        # create dictionary of lists of files; split by diagnosis
        pIdbydiagnosis[diag] = (list(diagnosis[diagnosis["label"] == diag]["ID"]))
        # one list of all the files
        train_list.extend(list(diagnosis[diagnosis["label"] == diag]["ID"]))

    if not os.path.exists(split_dir):
        os.makedirs(split_dir)

    train_split = os.path.join(split_dir, 'train.txt')
    test_split = os.path.join(split_dir, 'test.txt')
    with open(test_split, "w") as test:
        test_list = []
        # take a random sample for each label
        for diag in ds:
            test_list.extend(random.sample(pIdbydiagnosis[diag], distribution_dict[diag]))
        for pId in test_list:
            test.write(str(pId) + "\n")
        print("Test samples: " + str(len(test_list)))

    with open(train_split, "w") as train:
        # create train split by subtracting out test files
        train_list = list(Counter(train_list) - Counter(test_list))
        for pId in train_list:
            train.write(str(pId) + "\n")
        print("Train samples: " + str(len(train_list)))


def split(input_path='../data', split_dir='../data/splits', seed=252,
          distribution_dict=None):
    """create split files"""
    if distribution_dict is None or not distribution_dict:
        distribution_dict = {'COPD': 10, 'Healthy': 10, 'URTI': 3, 'Bronchiectasis': 2, 'Bronchiolitis': 2,
                             'Pneumonia': 2, 'LRTI': 1, 'Asthma': 0}
    if seed:
        random.seed(seed)
    # load diagnosis cv and extract unqiue diagnoses
    diag_csv = os.path.join(input_path, 'patient_diagnosis.csv')
    diagnosis = pd.read_csv(diag_csv, names=['pId', 'diagnosis'])
    ds = list(diagnosis['diagnosis'].unique())
    pIdbydiagnosis = {}
    train_list = []

    for diag in ds:
        # create dictionary of lists of files; split by diagnosis
        pIdbydiagnosis[diag] = (list(diagnosis[diagnosis["diagnosis"] == diag]["pId"]))
        # one list of all the files
        train_list.extend(list(diagnosis[diagnosis["diagnosis"] == diag]["pId"]))

    if not os.path.exists(split_dir):
        os.makedirs(split_dir)

    train_split = os.path.join(split_dir, 'train.txt')
    test_split = os.path.join(split_dir, 'test.txt')
    with open(test_split, "w") as test:
        test_list = []
        # take a random sample for each label
        for diag in ds:
            test_list.extend(random.sample(pIdbydiagnosis[diag], distribution_dict[diag]))
        for pId in test_list:
            test.write(str(pId) + "\n")
        print("Test samples: " + str(len(test_list)))

    with open(train_split, "w") as train:
        # create train split by subtracting out test files
        train_list = list(Counter(train_list) - Counter(test_list))
        for pId in train_list:
            train.write(str(pId) + "\n")
        print("Train samples: " + str(len(train_list)))


def contrastive_split(input_path='../', seed=252):
    if seed:
        random.seed(seed)
    # lungs
    print('lungs')
    diag_csv = os.path.join(input_path, 'data', 'processed', 'disease_labels.csv')
    diagnosis = pd.read_csv(diag_csv)
    diagnosis.reindex(columns=['ID', 'cycle', 'diagnosis', 'label'])

    for i, row in diagnosis.iterrows():
        if row["diagnosis"] == "Healthy":
            diagnosis.at[i, "label"] = -1
        else:
            diagnosis.at[i, "label"] = 1
    diagnosis = diagnosis[['ID', 'cycle', 'label']].rename(columns={"label": "diagnosis"})
    diagnosis.to_csv(os.path.join(input_path, 'data', 'processed', 'lung_labels.csv'), index=False)
    ds = list(diagnosis['diagnosis'].unique())
    pIdbydiagnosis = {}
    train_list = []

    for diag in ds:
        # create dictionary of lists of files; split by diagnosis
        pIdbydiagnosis[diag] = (list(diagnosis[diagnosis["diagnosis"] == diag]["ID"]))
        # one list of all the files
        train_list.extend(list(diagnosis[diagnosis["diagnosis"] == diag]["ID"]))
    fi.make_path(os.path.join(input_path, 'data', 'splits'))
    train_split = os.path.join(input_path, 'data', 'splits', 'train.txt')
    test_split = os.path.join(input_path, 'data', 'splits', 'test.txt')
    distribution_dict = {-1: 15, 1: 15}
    with open(test_split, "w") as test:
        test_list = []
        # take a random sample for each label
        for diag in ds:
            test_list.extend(random.sample(pIdbydiagnosis[diag], distribution_dict[diag]))
        for pId in test_list:
            test.write(str(pId) + "\n")
        print("Test samples: " + str(len(test_list)))

    with open(train_split, "w") as train:
        # create train split by subtracting out test files
        train_list = list(Counter(train_list) - Counter(test_list))
        for pId in train_list:
            train.write(str(pId) + "\n")
        print("Train samples: " + str(len(train_list)))

    # heartchallenge
    print('\nheartchallenge')
    diag_csv = os.path.join(input_path, 'heartchallenge', 'old_splits', 'heartchallenge_labels.csv')
    diagnosis = pd.read_csv(diag_csv).drop_duplicates(subset=["ID"]).reset_index()[["ID", "label"]]
    for i, row in diagnosis.iterrows():
        if row["label"] == "Normal":
            diagnosis.at[i, "label"] = -1
        else:
            diagnosis.at[i, "label"] = 1
    diagnosis.to_csv(os.path.join(input_path, 'heartchallenge', 'processed', 'heartchallenge_labels.csv'), index=False)
    ds = list(diagnosis['label'].unique())
    pIdbydiagnosis = {}
    train_list = []

    for diag in ds:
        # create dictionary of lists of files; split by diagnosis
        pIdbydiagnosis[diag] = (list(diagnosis[diagnosis["label"] == diag]["ID"]))
        # one list of all the files
        train_list.extend(list(diagnosis[diagnosis["label"] == diag]["ID"]))
    fi.make_path(os.path.join(input_path, 'heartchallenge', 'splits'))
    train_split = os.path.join(input_path, 'heartchallenge', 'splits', 'train.txt')
    test_split = os.path.join(input_path, 'heartchallenge', 'splits', 'test.txt')
    distribution_dict = {-1: 82, 1: 82}
    with open(test_split, "w") as test:
        test_list = []
        # take a random sample for each label
        for diag in ds:
            test_list.extend(random.sample(pIdbydiagnosis[diag], distribution_dict[diag]))
        for pId in test_list:
            test.write(str(pId) + "\n")
        print("Test samples: " + str(len(test_list)))

    with open(train_split, "w") as train:
        # create train split by subtracting out test files
        train_list = list(Counter(train_list) - Counter(test_list))
        for pId in train_list:
            train.write(str(pId) + "\n")
        print("Train samples: " + str(len(train_list)))

    # heart
    print('\nheart')
    diag_csv = os.path.join(input_path, 'heart', 'processed', 'heart_labels.csv')
    diagnosis = pd.read_csv(diag_csv).drop_duplicates(subset=["ID"]).reset_index()[["ID", "label"]]
    diagnosis.to_csv(os.path.join(input_path, 'heart', 'processed', 'heart_labels.csv'), index=False)
    ds = list(diagnosis['label'].unique())
    pIdbydiagnosis = {}
    train_list = []

    for diag in ds:
        # create dictionary of lists of files; split by diagnosis
        pIdbydiagnosis[diag] = (list(diagnosis[diagnosis["label"] == diag]["ID"]))
        # one list of all the files
        train_list.extend(list(diagnosis[diagnosis["label"] == diag]["ID"]))
    fi.make_path(os.path.join(input_path, 'heart', 'splits'))
    train_split = os.path.join(input_path, 'heart', 'splits', 'train.txt')
    pre_train_split = os.path.join(input_path, 'heart', 'splits', 'pre-train.txt')
    test_split = os.path.join(input_path, 'heart', 'splits', 'test.txt')
    distribution_dict = {-1: 324, 1: 324}
    with open(test_split, "w") as test:
        test_list = []
        # take a random sample for each label
        for diag in ds:
            sample = random.sample(pIdbydiagnosis[diag], distribution_dict[diag])
            test_list.extend(sample)
            pIdbydiagnosis[diag] = list(Counter(pIdbydiagnosis[diag]) - Counter(sample))
        for pId in test_list:
            test.write(str(pId) + "\n")
        print("Test samples: " + str(len(test_list)))

    with open(train_split, "w") as train:
        fine_tune_list = []
        for diag in ds:
            sample = random.sample(pIdbydiagnosis[diag], distribution_dict[diag])
            fine_tune_list.extend(sample)
        for pId in fine_tune_list:
            train.write(str(pId) + "\n")
        print("Train samples: " + str(len(fine_tune_list)))

    with open(pre_train_split, "w") as pre_train:
        # create train split by subtracting out test files
        pre_train_list = list(Counter(train_list) - Counter(test_list))
        for pId in pre_train_list:
            pre_train.write(str(pId) + "\n")
        print("Pre-train samples: " + str(len(pre_train_list)))


if __name__ == "__main__":
    contrastive_split()
    # # create splits based 30 tests of 10 patients each, OPTIONAL: provide a seed number for reproducibility and
    # # custom split dictionary with keys as strings matching raw diagnoses and values equaling desired number of that
    # # class in test set.
    # parser = argparse.ArgumentParser()
    # parser.add_argument('--data', default='../data', help='data path')
    # parser.add_argument('--split', default='../data/splits', help='location of output files')
    # parser.add_argument('--seed', default=252, help='seed for sampling')
    # parser.add_argument('--distribution', default=None, help='dict containing desired test samples from each '
    #                                                          'diagnosis class')
    # # distribution_dict = {'COPD': 10, 'Healthy': 10, 'URTI': 3, 'Bronchiectasis': 2, 'Bronchiolitis': 2,
    # # 'Pneumonia': 2, 'LRTI': 1, 'Asthma': 0}
    # args = parser.parse_args()
    #
    # fi.make_path(args.split)
    # split_heart(args.data, args.split, args.seed, args.distribution)
