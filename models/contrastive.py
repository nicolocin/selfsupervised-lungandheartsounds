from models import ResNetSimCLR, SSL, Logistic, CNN, CNNlight, DistillCNN
import os
import time
import numpy as np
import datetime
import argparse
import torch
from torch.nn import BCEWithLogitsLoss, Softmax
from sklearn.metrics import roc_auc_score, classification_report, confusion_matrix
from glob import glob
from scipy.special import expit
import torch.nn.functional as F
from data import get_data_loader, get_dataset, get_scikit_loader
from sklearn.neighbors import KNeighborsClassifier
import joblib
import sys
import copy
import matplotlib.pyplot as plt


from torch.utils.tensorboard import SummaryWriter

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.append("../utils")
import loss as lo
import labels as la
import file as fi
from loss import NTXentLoss, WeightedFocalLoss
import random


def add_kd_loss(student_logits, teacher_logits, temperature):
  #print(student_logits.shape)
  #print(teacher_logits.shape)
  teacher_probs = F.softmax(teacher_logits / temperature)
  #print(teacher_probs.shape)
  kd_loss = torch.mean(temperature**2 * F.binary_cross_entropy_with_logits(student_logits / temperature, teacher_probs))
  return kd_loss

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


class ContrastiveLearner(object):

    def __init__(self, dataset, epochs, batch_size, log_dir, model=None):
        self.device = self._get_device()
        self.dataset = dataset
        self.epochs = epochs
        self.log_dir = log_dir
        self.nt_xent_criterion = NTXentLoss()
        self.model = model
        self.batch_size = batch_size
        try:
            self.exp = dataset.exp
        except:
            self.exp = None

    def _get_device(self):
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print("Running on:", device)
        return device

    def get_model(self, out_dim, restore=True):
        model = ResNetSimCLR(out_dim=out_dim, base_model="resnet18").to(self.device)
        model = self._load_pre_trained_weights(model, restore=restore)
        return model

    def pre_train(self, log_file, task, label_file, augment=None, learning_rate=0., restore=False):
        test_dataset = get_dataset(task, label_file, base_dir, split="val")
        train_df = self.dataset.labels.reset_index()
        train_data = self.dataset.data
        test_df = test_dataset.labels.reset_index()
        test_data = test_dataset.data

        self.batch_size = min(self.batch_size, train_data.shape[0])
        if self.exp in [2, 4, 5, 6]:
            self.batch_size = 1
        print('Batch Size: {}'.format(self.batch_size))

        train_loader = get_data_loader(task, label_file, base_dir, batch_size=self.batch_size, split="pretrain",
                                       df=train_df, transform=augment, data=train_data, exp=self.exp)
        valid_loader = get_data_loader(task, label_file, base_dir, batch_size=self.batch_size, split="pretrain",
                                       df=test_df, transform=augment, data=test_data, exp=self.exp)

        if self.model is not None:
            model = self.model
        else:
            model = self.get_model(out_dim=256, restore=restore)

        optimizer = torch.optim.Adam(model.parameters(), learning_rate, weight_decay=1e-6)
        # scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=len(train_loader), eta_min=0,
                                                               #last_epoch=-1)

        best_valid_loss = np.inf
        best_auc = np.inf
        fi.make_path(os.path.join(log_dir, 'checkpoints'))
        torch.save(model.state_dict(),
                   os.path.join(self.log_dir, 'encoder.pth'))
        tb_dir = os.path.join(log_dir, 'runs', datetime.datetime.now().strftime("%m-%d-%H-%M-%S"))
        print("tensorboard_dir: {}".format(tb_dir))
        writer = SummaryWriter(
            log_dir=tb_dir)
        batch_counter = 0
        model.eval()
        train_X, train_y = get_scikit_loader(self.device, task, label_file, base_dir, "train", df=train_df,
                                             encoder=model, data=train_data)
        test_X, test_y = get_scikit_loader(self.device, task, label_file, base_dir, "train", df=test_df,
                                           encoder=model, data=test_data)
        train_X = np.asarray(train_X)
        train_y = np.asarray(train_y)
        test_X = np.asarray(test_X)
        test_y = np.asarray(test_y)
        writer.add_embedding(test_X.reshape((test_X.shape[0], -1)), metadata=test_y.tolist(), global_step=0)

        evaluator = KNeighborsClassifier(n_neighbors=10,weights='distance')
        evaluator.fit(train_X, train_y)

        try:
            roc_score = roc_auc_score(test_y, evaluator.predict_proba(test_X)[:, 1])
        except:
            roc_score = 0
        writer.add_scalar('AUC/pretrain', roc_score, 0)
        print(
            "pretrain KNN Acc: {:.3f}\n".format(roc_score))
        with open(log_file, "a+") as log:
            log.write("\t\tpretrain KNN Acc: {:.3f}\n".format(roc_score))
        model.train()

        for epoch_counter in range(1, self.epochs + 1):
            start = time.time()
            epoch_loss = 0
            if self.exp in [2, 4, 5, 6]:
                for i in range(4):
                    for data in train_loader:
                        if data == -1:
                            continue
                        xis, xjs = [], []
                        for sample in data:
                            xis.append(sample[0])
                            xjs.append(sample[1])
                        xis = torch.stack(tuple(xis))
                        xis = xis.view(xis.shape[0], xis.shape[2], xis.shape[3])
                        xjs = torch.stack(tuple(xjs))
                        xjs = xjs.view(xjs.shape[0], xjs.shape[2], xjs.shape[3])
                        xis = xis.to(self.device)
                        xjs = xjs.to(self.device)
                        loss = self._step(model, xis, xjs)
                        writer.add_scalar('loss/pretrain', loss, batch_counter)
                        loss.backward()
                        optimizer.step()
                        epoch_loss += loss
                        batch_counter += 1

            elif self.exp == 3:
                for i in range(90):
                    for xis, xjs, y in train_loader:
                        optimizer.zero_grad()
                        xis = xis.to(self.device)
                        xjs = xjs.to(self.device)

                        loss = self._step(model, xis, xjs, y)
                        writer.add_scalar('loss/pretrain', loss, batch_counter)
                        loss.backward()
                        optimizer.step()
                        epoch_loss += loss
                        batch_counter += 1

            elif self.exp in [0, 1]:
                for i in range(90):
                    for xis, xjs in train_loader:
                        optimizer.zero_grad()
                        xis = xis.to(self.device)
                        xjs = xjs.to(self.device)

                        loss = self._step(model, xis, xjs)
                        loss.backward()
                        optimizer.step()
                        epoch_loss += loss
                        writer.add_scalar('loss/pretrain', loss, batch_counter)
                        batch_counter += 1

            else:
                for xis, xjs in train_loader:
                    optimizer.zero_grad()
                    xis = xis.to(self.device)
                    xjs = xjs.to(self.device)

                    loss = self._step(model, xis, xjs)
                    loss.backward()
                    optimizer.step()
                    epoch_loss += loss
                    writer.add_scalar('loss/pretrain', loss, batch_counter)

                    # warmup for the first 10 epochs
                    # if epoch_counter > 10:
                    #     scheduler.step()
                    # for i, layer in enumerate(model.children()):
                    #     for j, param in enumerate(layer.parameters()):
                    #         writer.add_histogram('param_{}_{}'.format(i, j), param, batch_counter)
                    # writer.add_images('xi', torch.unsqueeze(xis, -3), batch_counter)
                    # writer.add_images('xj', torch.unsqueeze(xjs, -3), batch_counter)
                    batch_counter += 1

            epoch_loss /= float(batch_counter)
            # validate the model
            valid_loss = self._validate(model, valid_loader)
            if valid_loss < best_valid_loss:
                # save the model weights
                best_valid_loss = valid_loss
                torch.save(model.state_dict(),
                           os.path.join(self.log_dir, 'checkpoints', 'encoder_{}.pth'.format(epoch_counter)))


            writer.add_embedding(test_X.reshape((test_X.shape[0], -1)), metadata=test_y.tolist(), global_step=epoch_counter)

            model.eval()
            train_X, train_y = get_scikit_loader(self.device, task, label_file, base_dir, "train", df=train_df,
                                                 encoder=model, data=train_data)
            test_X, test_y = get_scikit_loader(self.device, task, label_file, base_dir, "train", df=test_df,
                                               encoder=model, data=test_data)
            train_X = np.asarray(train_X)
            train_y = np.asarray(train_y)
            test_X = np.asarray(test_X)
            test_y = np.asarray(test_y)
            evaluator = KNeighborsClassifier(n_neighbors=10, weights='distance')
            evaluator.fit(train_X, train_y)

            try:
                roc_score = roc_auc_score(test_y, evaluator.predict_proba(test_X)[:, 1])
            except:
                roc_score = 0
            writer.add_scalar('AUC/pretrain', roc_score, epoch_counter)
            model.train()

            fold_train_acc = evaluator.score(test_X, test_y)
            if roc_score < best_auc:
                # save the model weights
                best_auc = roc_score
                torch.save(model.state_dict(),
                           os.path.join(self.log_dir, 'encoder.pth'))
            elapsed = time.time() - start
            print("\tEpoch: {:03d}, Time: {:.3f} s".format(epoch_counter, elapsed))
            print("\t\tTrain loss: {:.7f}\tVal loss: {:.7f}".format(epoch_loss, valid_loss))
            with open(log_file, "a+") as log:
                log.write(
                    "\tEpoch: {:03d}\tTrain Loss: {:.7f}\tVal Loss: {:.7f}\n".format(
                        epoch_counter, epoch_loss, valid_loss
                    )
                )
            print(
                "\t\tpretrain KNN Acc: {:.3f}\t KNN AUC: {:.3f}\n".format(fold_train_acc, roc_score))
            with open(log_file, "a+") as log:
                log.write("\t\tpretrain KNN Acc: {:.3f}\t KNN AUC: {:.3f}\n".format(fold_train_acc, roc_score))


            # cosine_lr_decay = scheduler.get_lr()[0]
            # print("\t\tcosine lr decay: {:.10f}".format(cosine_lr_decay))
            # with open(log_file, "a+") as log:
            #     log.write("\tcosine lr decay: {:.10f}\n".format(cosine_lr_decay))
            # # if counter > 10:
            #     print("Early stop...")
            #     break

        return model

    def fine_tune(self, n_splits, task, label_file, log_file, augment=None, encoder=None, evaluator_type=None,
                  learning_rate=0.0, model_num=0):
        df = self.dataset.labels
        data = self.dataset.data
        total_train_acc = 0
        total_test_acc = 0
        self.batch_size = min(self.batch_size, data.shape[0])
        print('Batch Size: {}'.format(self.batch_size))
        test_dataset = get_dataset(task, label_file, base_dir, split="val")

        if model_num == 0:
            model_id = len(glob(os.path.join(log_dir, "evaluator_*")))
        else:
            model_id = model_num

        print("training model with id: {}".format(model_id))
        weights = torch.as_tensor(la.class_distribution(task, label_file)).float().to(self.device)
        # weights = 1.0 / weights
        # weights = weights / weights.sum()
        pos_weight = torch.tensor(weights[0].item() / weights[1].item()).to(self.device)
        #loss = BCEWithLogitsLoss().to(self.device)
        loss = BCEWithLogitsLoss(pos_weight=pos_weight).to(self.device)
        # pos_weight = torch.tensor(weights[1].item() / (weights[0].item() + weights[1].item())).to(self.device)
        # loss = WeightedFocalLoss(alpha=pos_weight).to(self.device)
        writer = SummaryWriter(log_dir=os.path.join(log_dir, 'runs', str(model_id)))
        valid_auc_counter = 0
        train_auc = 0
        test_auc = 0
        train_df = df
        test_df = test_dataset.labels
        train_data = data
        test_data = test_dataset.data

        sanity_loader = get_data_loader(task, label_file, base_dir, 1, "test")

        total_train_acc = 0

        if evaluator_type == "linear":
            train_X, train_y = get_scikit_loader(self.device, task, label_file, base_dir, "train", train_df,
                                                 encoder, data=train_data)
            id, test_X, test_y = get_scikit_loader(self.device, task, label_file, base_dir, "val", test_df,
                                                   encoder, data=test_data)
            _, X, y = get_scikit_loader(self.device, task, label_file, base_dir, "test", encoder=encoder)

            if encoder is not None:
                for layer in encoder.children():
                    for param in layer.parameters():
                        param.requires_grad = False
                model = Logistic(encoder.num_ftrs).to(self.device)
            else:
                print("No encoder provided. Supervised Training...\n")
                model = Logistic(train_X[0].shape[0]).to(self.device)

            # optimizer = torch.optim.LBFGS(model.parameters(), history_size=10, max_iter=4,
            #                            lr=10 * learning_rate)
            optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-2)
            counter = 0
            best_test_loss = np.inf
            epoch = 0
            for epoch in range(1, self.epochs + 1):
                start = time.time()
                train_loss, train_true, train_pred = self._optimize(model, train_X, train_y, optimizer,
                                                                    self.device,
                                                                    loss)
                test_loss, test_true, test_pred = self._predict(model, id, test_X, test_y, self.device, loss)
                sanity_loss, sanity_true, sanity_pred = self._predict(model, _, X, y, self.device, loss)
                sanity_pred = expit(sanity_pred)
                train_pred, test_pred = expit(train_pred), expit(test_pred)
                train_accuracy = lo.get_accuracy(train_true, train_pred)
                test_accuracy = lo.get_accuracy(test_true, test_pred)
                if test_loss < best_test_loss:
                    lo.save_weights(model, os.path.join(log_dir, "evaluator_" + str(model_id) + ".pt"))
                    best_test_loss = test_loss
                    counter = 0
                else:
                    counter += 1
                try:
                    test_roc_score = roc_auc_score(test_true, test_pred)
                    train_roc_score = roc_auc_score(train_true, train_pred)
                    sanity_roc_score = roc_auc_score(sanity_true, sanity_pred)
                    writer.add_scalar('AUC/test', sanity_roc_score, epoch)
                    writer.add_scalar('loss/test', sanity_loss, epoch)
                    train_auc += train_roc_score
                    test_auc += test_roc_score
                    valid_auc_counter += 1

                    writer.add_scalar('AUC/train', train_roc_score, epoch)
                    writer.add_scalar('AUC/val', test_roc_score, epoch)
                    writer.add_scalar('loss/train', train_loss, epoch)
                    writer.add_scalar('loss/val', test_loss, epoch)
                except:
                    pass
                # if counter == self.epochs//5:
                #     print("Early stop...")
                #     break
                total_train_acc += train_accuracy
                total_test_acc += test_accuracy

        elif evaluator_type == "fine-tune":
            if encoder is not None:
                model = SSL(encoder).to(self.device)
            else:
                print("No encoder provided. Supervised Training...\n")
                model = self.get_model(1)

            train_loader = get_data_loader(task, label_file, base_dir, self.batch_size, "train", df=train_df,
                                           data=train_data)
            test_loader = get_data_loader(task, label_file, base_dir, 1, "val", df=test_df, data=test_data)
            optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-2)

            counter = 0
            best_test_loss = np.inf
            #best_test_auc = 0
            epoch = 0
            counter = 0
            for epoch in range(1, self.epochs + 1):
                start = time.time()
                train_loss, train_true, train_pred = self._train(model, train_loader, optimizer, self.device,
                                                                 loss)
                test_loss, test_true, test_pred = self._test(model, test_loader, self.device, loss)
                sanity_loss, sanity_true, sanity_pred = self._test(model, sanity_loader, self.device, loss)
                sanity_pred = expit(sanity_pred)

                train_pred, test_pred = expit(train_pred), expit(test_pred)
                train_accuracy = lo.get_accuracy(train_true, train_pred)
                test_accuracy = lo.get_accuracy(test_true, test_pred)


                # if counter == self.epochs//5:
                #     print("Early stop...")
                #     break
                try:
                    test_roc_score = roc_auc_score(test_true, test_pred)
                    train_roc_score = roc_auc_score(train_true, train_pred)
                    sanity_roc_score = roc_auc_score(sanity_true, sanity_pred)
                    writer.add_scalar('AUC/test', sanity_roc_score, epoch)
                    writer.add_scalar('loss/test', sanity_loss, epoch)
                    train_auc += train_roc_score
                    test_auc += test_roc_score
                    valid_auc_counter += 1

                    writer.add_scalar('AUC/train', train_roc_score, epoch)
                    writer.add_scalar('AUC/val', test_roc_score, epoch)
                    writer.add_scalar('loss/train', train_loss, epoch)
                    writer.add_scalar('loss/val', test_loss, epoch)
                except:
                    test_roc_score = 0

                if test_loss < best_test_loss:
                    lo.save_weights(model, os.path.join(log_dir, "evaluator_" + str(model_id) + ".pt"))
                    best_test_loss = test_loss
                    counter = 0
                else:
                    counter += 1

                total_train_acc += train_accuracy
                total_test_acc += test_accuracy
                elapsed = time.time() - start

                print("\tEpoch: {:03d}, Time: {:.3f} s".format(epoch, elapsed))
                print("\t\tTrain BCE: {:.7f}\tVal BCE: {:.7f}".format(train_loss, test_loss))
                print("\t\tTrain Acc: {:.7f}\tVal Acc: {:.7f}\tROC: {:.7f}\n".format(train_accuracy,
                                                                                     test_accuracy, test_roc_score))
                with open(log_file, "a+") as log:
                    log.write(
                        "\tEpoch: {:03d}\tTrain Loss: {:.7f}\tVal Loss: {:.7f}\tTrain Acc: {:.7f}\tVal Acc: {:.7f}\tROC: {:.7f}\n".format(
                            epoch, train_loss, test_loss, train_accuracy, test_accuracy, test_roc_score
                        )
                    )

        train_auc /= valid_auc_counter
        test_auc /= valid_auc_counter

        del model
        torch.cuda.empty_cache()

        print(
            "Total Cross Val Train Acc: {:.7f}\tTotal Cross Val Test Acc: {:.7f}\n".format(total_train_acc / epoch,
                                                                                           total_test_acc / epoch))
        with open(log_file, "a+") as log:
            log.write(
                "Total Cross Val Train Acc: {:.7f}\tTotal Cross Val Test Acc: {:.7f}\n".format(
                    total_train_acc / epoch, total_test_acc / epoch
                )
            )

        print(
            "Total Cross Val Train auc: {:.7f}\tTotal Cross Val Test auc: {:.7f}\n".format(train_auc,
                                                                                           test_auc))
        with open(log_file, "a+") as log:
            log.write(
                "Total Cross Val Train auc: {:.7f}\tTotal Cross Val Test auc: {:.7f}\n".format(train_auc,
                                                                                               test_auc))

    def distill(self, n_splits, task, label_file, log_file, augment=None, teacher=None, evaluator_type=None,
                learning_rate=0.0):

        # Pretrain total df and data
        df = self.dataset.labels
        data = self.dataset.data

        splits_dir = os.path.join(base_dir, "splits")
        pretrain_only_text_file = os.path.join(splits_dir, "pretrain-only.txt")
        with open(pretrain_only_text_file, "r") as pretrain_only_list:
            pretrain_only_IDs = set([line.strip() for line in pretrain_only_list])
        train_df = df[df.ID.isin(pretrain_only_IDs)]
        train_data = data.take(train_df.index.tolist(), axis=0)
        # train_df = df
        # train_data = data

        val_dataset = get_dataset(task, label_file, base_dir, split="val")
        val_df = val_dataset.labels
        val_data = val_dataset.data
        print('Batch Size: {}'.format(self.batch_size))
        if evaluator_type == 'cnn':
            model = CNN(task, 1).to(self.device)
            # Include option for loading a model:
            # Put model in eval mode and use model.load_state_dict()
        elif evaluator_type == 'cnn-light':
            model = CNNlight(task, 1).to(self.device)
        elif evaluator_type == 'distill-cnn':
            model = DistillCNN(task, 1).to(self.device)

        train_loader = get_data_loader(task, label_file, base_dir, self.batch_size, "train", df=train_df,
                                       data=train_data)  # Put in train mode to get X and y from pretrain data
        val_loader = get_data_loader(task, label_file, base_dir, 1, "val", df=val_df, data=val_data)
        loss = BCEWithLogitsLoss().to(self.device)
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        writer = SummaryWriter(log_dir=os.path.join(log_dir, 'runs', datetime.datetime.now().strftime("%m-%d-%H-%M-%S")))

        best_dev_auc = 0
        # Supervised Learning Algorithm for the Student Model:
        for epoch in range(1, self.epochs + 1):
            start = time.time()
            train_loss, train_true, train_pred = self._distill(model, teacher, train_loader, optimizer, self.device,
                                                               loss)
            val_loss, val_true, val_pred = self._test(model, val_loader, self.device, loss)

            train_pred, val_pred = expit(train_pred), expit(val_pred)
            train_accuracy = lo.get_accuracy(train_true, train_pred)
            val_accuracy = lo.get_accuracy(val_true, val_pred)

            train_auc_score = roc_auc_score(train_true, train_pred)
            val_auc_score = roc_auc_score(val_true, val_pred)

            # Do runs for teacher (using _test function)
            # teacher_train_loss, teacher_train_true, teacher_train_pred = self._test(teacher, train_loader, self.device,
            #                                                                         loss)
            # teacher_val_loss, teacher_val_true, teacher_val_pred = self._test(teacher, val_loader, self.device,
            #                                                                   loss)
            # teacher_train_pred, teacher_val_pred = expit(teacher_train_pred), expit(teacher_val_pred)
            # teacher_train_acc = lo.get_accuracy(teacher_train_true, teacher_train_pred)
            # teacher_val_acc = lo.get_accuracy(teacher_val_true, teacher_val_pred)
            # teacher_train_auc_score = roc_auc_score(teacher_train_true, teacher_train_pred)
            # teacher_val_auc_score = roc_auc_score(teacher_val_true, teacher_val_pred)
            # End teacher calculations

            if best_dev_auc < val_auc_score:
                lo.save_weights(model, os.path.join(log_dir, "student_pretrain_gen_testing" + ".pt"))
                best_dev_auc = val_auc_score

            num_teacher_params = count_parameters(teacher)
            num_student_params = count_parameters(model)
            elapsed = time.time() - start

            print("\tEpoch: {:03d}, Time: {:.3f} s".format(epoch, elapsed))
            print("\t\tTrain Loss: {:.7f}\tVal Loss: {:.7f}".format(train_loss, val_loss))
            print("\t\tTrain Acc: {:.7f}\tVal Acc: {:.7f}\tTrain AUC: {:.7f}\tVal AUC: {:.7f}\n".format(train_accuracy,
                                                                                                        val_accuracy,
                                                                                                        train_auc_score,
                                                                                                        val_auc_score))
            print("\t\tNumber of student params: {:.7f}\tNumber of teacher params: {:.7f}".format(num_student_params,
                                                                                                  num_teacher_params))

            # Prints for teacher:
            # print("Teacher results: (Should be same b/t iterations)")
            # print("\t\tTrain Loss: {:.7f}\tVal Loss: {:.7f}".format(teacher_train_loss, teacher_val_loss))
            # print("\t\tTrain Acc: {:.7f}\tVal Acc: {:.7f}\tTeacher Train AUC: {:.7f}\tTeacher Val AUC: {:.7f}\n".format(
            #     teacher_train_acc, teacher_val_acc,
            #     teacher_train_auc_score, teacher_val_auc_score))
            # End teacher prints

            writer.add_scalar('loss/train', train_loss, epoch)
            writer.add_scalar('loss/val', val_loss, epoch)

            writer.add_scalar('accuracy/train', train_accuracy, epoch)
            writer.add_scalar('accuracy/val', val_accuracy, epoch)

            writer.add_scalar('AUC/train', train_auc_score, epoch)
            writer.add_scalar('AUC/val', val_auc_score, epoch)
            writer.add_scalar('student_params', num_student_params, epoch)

            with open(log_file, "a+") as log:
                log.write(
                    "\tEpoch: {:03d}\tTrain Loss: {:.7f}\tVal Loss: {:.7f}\tTrain Acc: {:.7f}\tVal Acc: {:.7f}\tVal AUC: {:.7f}\n".format(
                        epoch, train_loss, val_loss, train_accuracy, val_accuracy, val_auc_score
                    )
                )

    def test(self, task, label_file, log_file, encoder, evaluator_dir, evaluator_type=None, model_num=0):
        _y_pred = []
        weights = torch.as_tensor(la.class_distribution(task, label_file)).float().to(self.device)
        # weights = 1.0 / weights
        # weights = weights / weights.sum()
        # pos_weight = torch.tensor(weights[0].item() / weights[1].item()).to(self.device)
        loss = BCEWithLogitsLoss().to(self.device)
        # pos_weight = torch.tensor(weights[1].item() / (weights[0].item() + weights[1].item())).to(self.device)
        # loss = WeightedFocalLoss(alpha=pos_weight).to(self.device)

        labels = ["Normal", "Abnormal"]

        # scaler = preprocessing.StandardScaler()
        data = self.dataset.data
        # scaler.fit(data.reshape((data.shape[0], -1)))
        # scaler.transform(data)

        scikit_eval = len(glob(os.path.join(evaluator_dir, "evaluator_*.pkl"))) > 0
        distill_eval = len(glob(os.path.join(evaluator_dir, "student_*.pt"))) > 0

        with open(log_file, "a+") as out:
            if scikit_eval:
                print('SK Model Test')
                encoder.eval()
                id, X, y = get_scikit_loader(self.device, task, label_file, base_dir, split="test", encoder=encoder,
                                             data=data)
                X = np.asarray(X)
                y = np.asarray(y)
                y_true = y
                for i, model_weights in enumerate(glob(os.path.join(evaluator_dir, "evaluator_*.pkl"))):
                    model = joblib.load(model_weights)
                    y_pred = model.predict_proba(X)[:, 1]
                    _y_pred.append(y_pred)
                    # loss = loss.cuda()
                    # ce = loss(torch.as_tensor(y_pred).to(self.device).view(-1).float(),
                    #           torch.as_tensor(y).to(self.device).view(-1).float())
                    # print("Model {} Test BCE: {:.7f}".format(i, ce))
                    # out.write("Model {} Test BCE: {:.7f}\n".format(i, ce))

            elif distill_eval:
                encoder.eval()
                print('Distill Testing')
                model_weights = os.path.join(evaluator_dir, "studentONLY_new_pretrain_distill_baseline.pt")
                loader = get_data_loader(task, label_file, base_dir, batch_size=self.batch_size, split="test",
                                         data=data)
                model = DistillCNN(task, 1).to(self.device)
                state_dict = torch.load(model_weights)
                model.load_state_dict(state_dict)
                model.eval()

                # state_dict = torch.load(os.path.join(log_dir, 'evaluator_FINETUNE2.pt'))
                # encoder = self.get_model(256)
                # teacher = SSL(encoder)
                # teacher.load_state_dict(state_dict)
                # teacher.to(self.device).eval()
                # ce, y_true, y_pred = self._test(teacher, loader, self.device, loss, log_file)

                ce, y_true, y_pred = self._test(model, loader, self.device, loss, log_file)

                _y_pred.append(expit(y_pred))

                print("Model {} Test BCE: {:.7f}".format(1, ce))
                out.write("Model {} Test BCE: {:.7f}\n".format(1, ce))

            else:
                model_weights = os.path.join(evaluator_dir, "evaluator_{}.pt".format(model_num))
                loader = get_data_loader(task, label_file, base_dir, batch_size=self.batch_size, split="test",
                                         data=data)
                if evaluator_type == 'fine-tune':
                    if encoder is None:
                        model = self.get_model(1)
                    else:
                        model = SSL(encoder).to(self.device)
                    state_dict = torch.load(model_weights)
                    model.load_state_dict(state_dict)
                    model.eval()
                    ce, y_true, y_pred = self._test(model, loader, self.device, loss, log_file)
                else:
                    del loader
                    id, X, y = get_scikit_loader(self.device, task, label_file, base_dir, split="test",
                                                 encoder=encoder, data=data)
                    if encoder is None:
                        model = Logistic(X[0].shape[0]).to(self.device)
                    else:
                        model = Logistic(encoder.num_ftrs).to(self.device)
                    state_dict = torch.load(model_weights)
                    model.load_state_dict(state_dict)
                    model.eval()
                    ce, y_true, y_pred = self._predict(model, id, X, y, self.device, loss, log_file)
                _y_pred.append(expit(y_pred))
                print("Model {} Test BCE: {:.7f}".format(1, ce))
                out.write("Model {} Test BCE: {:.7f}\n".format(1, ce))
            _y_pred = np.average(_y_pred, axis=0)
            # auc_cat = lo.auc_per_cat(y_true, expit(_y_pred), labels)
            # out.write("\nPer category AUC:")
            # print("\nPer category AUC:")
            # for key in auc_cat.keys():
            # out.write("{}: {:.3f}\n".format(key, auc_cat[key]))
            # print("{}: {:.3f}\n".format(key, auc_cat[key]))
            # print(y_true[:10], _y_pred[:10], min(_y_pred), max(_y_pred))
            roc_score = roc_auc_score(y_true, _y_pred)

            # fpr, tpr, _ = roc_curve(y_true, _y_pred)
            # plt.plot(fpr, tpr, marker='.')
            # plt.show()

            report = classification_report(y_true, np.round(_y_pred), target_names=labels)
            conf_matrix = confusion_matrix(y_true, np.round(_y_pred))
            print("Ensemble AUC-ROC: {:.7f}\n{}\nConfusion Matrix:\n{}\n".format(roc_score, report, conf_matrix))
            out.write(
                "Seed: {}\tFolds: {}\nEnsemble AUC-ROC: {:.7f}\n{}\nConfusion Matrix:\n{}\n".format(seed, 1,
                                                                                                    roc_score, report,
                                                                                                    conf_matrix))

            # id, scikit_X, scikit_y = get_scikit_loader(self.device, task, label_file, base_dir, split="test")
            # baseline = DummyClassifier(strategy="most_frequent")
            # baseline.fit(scikit_X, scikit_y)
            # baseline_pred = baseline.predict(scikit_X)
            # report = classification_report(scikit_y, baseline_pred, target_names=labels, zero_division=0)
            # roc_score = roc_auc_score(scikit_y, baseline_pred)
            # print("Baseline:\nAUC-ROC: {:.7f}\n{}\n".format(roc_score, report))
            # out.write("Baseline:\nAUC-ROC: {:.7f}\n{}\n".format(roc_score, report))
        return roc_score

    def _load_pre_trained_weights(self, model, restore=False):
        try:
            if restore:
                state_dict = torch.load(os.path.join(self.log_dir, 'encoder.pth'))
                model.load_state_dict(state_dict)
                print("Loaded pretrained model with success.")
            else:
                raise FileNotFoundError
        except FileNotFoundError:
            print("pretrained weights not found.")

        return model

    def _train(self, model, loader, optimizer, device, loss):
        model.train()
        y_true = []
        y_pred = []
        for i, data in enumerate(loader):
            X, y = data
            X, y = X.view(X.shape[0], 1, X.shape[1], X.shape[2]).to(device), y.to(device).float()
            # print(X.mean(),X.std(),y)
            optimizer.zero_grad()
            output = model(X).float()
            train_loss = loss(output.view(-1), y.view(-1))
            y_true.extend(y.tolist())
            y_pred.extend(output.tolist())
            train_loss = train_loss.cuda()
            train_loss.backward()
            optimizer.step()
        ce = loss(torch.tensor(y_pred).to(device).float().view(-1), torch.tensor(y_true).to(device).float().view(-1))
        # print(y_pred)
        return ce, y_true, y_pred

    def _distill(self, model, teacher, loader, optimizer, device, loss):
        # TODO: Testing
        model.train()
        teacher.to(self.device).eval()
        y_true = []
        y_pred = []

        print("Number of batches is:")
        print(len(loader))
        print("*******")
        for i, data in enumerate(loader):
            X, y = data
            X, y = X.view(X.shape[0], 1, X.shape[1], X.shape[2]).to(device), y.to(device).float()
            y_reshaped = torch.reshape(y, (y.shape[0], 1))
            print("Iteration number: " + str(i))

            target_y = teacher(X)
            target_y_reshaped = torch.reshape(target_y, (target_y.shape[0], 1))  # Logit values
            # print("target_y is:")
            # print(target_y_reshaped)

            target_probs = expit(target_y_reshaped.cpu().detach().numpy())
            target_probs_tensor = torch.from_numpy(target_probs).to(self.device)
            # print("Teacher Prediction is:")
            # print(target_probs_tensor)

            student_y = model(X)
            # print("student_y is:")
            # print(student_y)
            student_probs = expit(student_y.cpu().detach().numpy())
            student_probs_tensor = torch.from_numpy(student_probs)
            # print("Student Prediction is:")
            # print(student_probs_tensor)

            if i % 20 == 0:
                print("target_y is:")
                print(target_y_reshaped)
                print("Teacher Prediction is:")
                print(target_probs_tensor)
                print("student_y is:")
                print(student_y)
                print("Student Prediction is:")
                print(student_probs_tensor)
                print("Actual y values are:")
                print(y)

            # Calculate the loss:
            optimizer.zero_grad()
            train_loss = loss(student_y, target_probs_tensor)
            # train_loss = add_kd_loss(student_y, target_y_reshaped, .1)
            # print("Iteration loss is:")
            # print(train_loss)

            # Put the actual predictions in y_pred:
            y_true.extend(y.tolist())
            y_pred.extend(student_y.tolist())
            # print(y_true)
            # print(y_pred)

            # Backprop:
            train_loss = train_loss.cuda()
            train_loss.backward()
            optimizer.step()

        # print("Loop finished successfully")
        ce = loss(torch.tensor(y_pred).to(device).float().view(-1), torch.tensor(y_true).to(device).float().view(-1))
        # ce = add_kd_loss(torch.tensor(y_pred).to(device).float().view(-1), torch.tensor(y_true).to(device).float().view(-1), .1)
        return ce, y_true, y_pred

    def _optimize(self, model, X, y, optimizer, device, loss):
        model.train()
        y_true = []
        y_pred = []
        X, y = torch.tensor(X).to(device).float(), torch.tensor(y).to(device).float()

        # print(X.mean(),X.std(),y)
        def closure():
            if torch.is_grad_enabled():
                optimizer.zero_grad()
            output = model(X).float()
            train_loss = loss(output.view(-1), y.view(-1))
            y_true.extend(y.tolist())
            y_pred.extend(output.tolist())
            train_loss = train_loss.cuda()
            train_loss.backward()
            return train_loss

        optimizer.step(closure)

        ce = loss(torch.tensor(y_pred).to(device).float().view(-1), torch.tensor(y_true).to(device).float().view(-1))
        # print(y_pred)
        return ce, y_true, y_pred

    @torch.no_grad()
    def _test(self, model, loader, device, loss, log_file=None):

        model.eval()
        y_true = []
        y_pred = []
        if log_file is not None:
            with open(log_file, 'a+') as f:
                f.write("ID,pred_proba,label\n")
        for i, data in enumerate(loader):
            try:
                id, X, y = data
            except:
                X,y = data
            X, y = X.view(X.shape[0], 1, X.shape[1], X.shape[2]).to(device), y.to(device)
            output = model(X)

            if log_file is not None:
                with open(log_file, 'a+') as f:
                    f.write('{},{:.3f},{}\n'.format(id[0], expit(output.cpu()).item(), y.item()))

            y_true.extend(y.tolist())
            y_pred.extend(output.tolist())

        ce = loss(torch.tensor(y_pred).to(device).float().view(-1),
                  torch.tensor(y_true).to(device).unsqueeze(1).float().view(-1))
        return ce, y_true, y_pred

    @torch.no_grad()
    def _predict(self, model, id, X, y, device, loss, log_file=None):

        model.eval()
        y_true = []
        y_pred = []
        if log_file is not None:
            with open(log_file, 'a+') as f:
                f.write("ID,pred_proba,label\n")
        X, y = torch.tensor(X).to(device).float(), torch.tensor(y).to(device).float()
        output = model(X)

        if log_file is not None:
            with open(log_file, 'a+') as f:
                for i in range(len(id)):
                    f.write('{},{:.3f},{}\n'.format(id[i], expit(output[i].cpu()).item(), y[i].item()))

        y_true.extend(y.tolist())
        y_pred.extend(output.tolist())
        ce = loss(torch.tensor(y_pred).to(device).float().view(-1),
                  torch.tensor(y_true).to(device).unsqueeze(1).float().view(-1))
        return ce, y_true, y_pred

    def _step(self, model, xis, xjs, y=None):
        xis = xis.view(xis.shape[0], 1, xis.shape[1], xis.shape[2]).to(self.device)
        xjs = xjs.view(xjs.shape[0], 1, xjs.shape[1], xjs.shape[2]).to(self.device)

        # a,b,c=plt.hist(xis[0].reshape(-1).cpu().detach().numpy(),10)
        # plt.show()
        # a,b,c=plt.hist(xjs[0].reshape(-1).cpu().detach().numpy(),10)
        # plt.show()

        # get the representations and the projections
        zis = model(xis)  # [N,C]
        # get the representations and the projections
        zjs = model(xjs)  # [N,C]
        # normalize projection feature vectors
        zis = F.normalize(zis, dim=1).unsqueeze(1)
        zjs = F.normalize(zjs, dim=1).unsqueeze(1)
        inputs = torch.cat((zis, zjs), dim=1) # shape = [batch_size, 2, encode_dim]
        loss = self.nt_xent_criterion(inputs, labels=y)
        return loss

    def _validate(self, model, valid_loader):

        # validation steps
        with torch.no_grad():
            model.eval()
            valid_loss = 0.0
            counter = 0
            if self.exp in [2, 4, 5, 6]:
                for data in valid_loader:
                    if data == -1:
                        continue
                    xis, xjs = [], []
                    for sample in data:
                        xis.append(sample[0])
                        xjs.append(sample[1])
                    xis = torch.stack(tuple(xis))
                    xis = xis.view(xis.shape[0], xis.shape[2], xis.shape[3])
                    xjs = torch.stack(tuple(xjs))
                    xjs = xjs.view(xjs.shape[0], xjs.shape[2], xjs.shape[3])
                    xis = xis.to(self.device)
                    xjs = xjs.to(self.device)
                    loss = self._step(model, xis, xjs)
                    valid_loss += loss.item()
                    counter += 1
                try:
                    valid_loss /= counter
                except:
                    valid_loss = -1
            elif self.exp == 3:
                for xis, xjs, y in valid_loader:
                    xis = xis.to(self.device)
                    xjs = xjs.to(self.device)
                    loss = self._step(model, xis, xjs, y)
                    valid_loss += loss.item()
                    counter += 1
                valid_loss /= counter
            else:
                for xis, xjs in valid_loader:
                    xis = xis.to(self.device)
                    xjs = xjs.to(self.device)
                    loss = self._step(model, xis, xjs)
                    valid_loss += loss.item()
                    counter += 1
                valid_loss /= counter
        model.train()
        return valid_loss


def pretrain_(epochs, task, base_dir, log_dir, augment, train_prop=1, exp=None, restore=False):
    log_file = os.path.join(log_dir, f"pretraintrain_log_prop_{train_prop}.txt")

    num_epochs = epochs
    batch_size = 16
    if train_prop == .01:
        batch_size = 5
    learning_rate = .00001

    with open(os.path.join(log_dir, f"pretrain_params_prop_{train_prop}.txt"), "w") as f:
        f.write(f"Epochs: {num_epochs}\n")
        f.write(f"Batch size: {batch_size}\n")
        f.write(f"Learning rate: {learning_rate}\n")
        f.write(f"Proportion of training data: {train_prop}\n")

    label_file = os.path.join(base_dir, "processed", "{}_labels.csv".format(task))
    dataset = get_dataset(task, label_file, base_dir, split="pretrain", train_prop=train_prop, exp=exp)

    learner = ContrastiveLearner(dataset, num_epochs, batch_size, log_dir)
    learner.pre_train(log_file, task, label_file, augment, learning_rate, restore=restore)


def train_(epochs, task, base_dir, log_dir, evaluator, augment, folds=5, train_prop=1, full_data=False, model_num=0):
    log_file = os.path.join(log_dir, f"train_log_{task}_{model_num}.txt")

    num_epochs = epochs
    batch_size = 16
    learning_rate = .00001
    label_file = os.path.join(base_dir, "processed", "{}_labels.csv".format(task))
    if evaluator is not None:
        print("Evaluator: " + evaluator)

    with open(os.path.join(log_dir, f"train_params_{task}_{model_num}.txt"), "w") as f:
        f.write(f"Model: {model_num}\n")
        f.write(f"Task: {task}\n")
        f.write(f"Label File: {label_file}\n\n")

        f.write(f"Folds: {folds}\n")
        f.write(f"Epochs: {num_epochs}\n")
        f.write(f"Batch size: {batch_size}\n")
        f.write(f"Learning rate: {learning_rate}\n")
        f.write(f"Proportion of training data: {train_prop}\n")
        f.write(f"Evaluator: {evaluator}\n")

    if not full_data:
        dataset = get_dataset(task, label_file, base_dir, split="train", train_prop=train_prop)
    else:
        dataset = get_dataset(task, label_file, base_dir, split="pretrain", train_prop=train_prop)

    learner = ContrastiveLearner(dataset, num_epochs, batch_size, log_dir)
    try:
        state_dict = torch.load(os.path.join(log_dir, 'encoder.pth'))
        encoder = learner.get_model(256)
        encoder.load_state_dict(state_dict)
    except FileNotFoundError:
        encoder = None
    learner.fine_tune(folds, task, label_file, log_file, augment, encoder, evaluator, learning_rate, model_num)


def distill_(epochs, task, base_dir, log_dir, evaluator, augment, folds=5, train_prop=1, full_data=False):
    log_file = os.path.join(log_dir, f"distill_log.txt")

    num_epochs = epochs
    batch_size = 16
    learning_rate = 0.0001
    if evaluator is not None:
        print("Evaluator: " + evaluator)
    with open(os.path.join(log_dir, "train_params.txt"), "w") as f:
        f.write(f"Folds: {folds}\n")
        f.write(f"Epochs: {num_epochs}\n")
        f.write(f"Batch size: {batch_size}\n")
        f.write(f"Learning rate: {learning_rate}\n")
        f.write(f"Proportion of training data: {train_prop}\n")
        f.write(f"Evaluator: {evaluator}\n")

    label_file = os.path.join(base_dir, "processed", "{}_labels.csv".format(task))
    #Will have to change this if I want to do runs on pretrain dataset:
    # if not full_data:
    #     dataset = get_dataset(task, label_file, base_dir, split="train", train_prop=train_prop)
    # else:
    dataset = get_dataset(task, label_file, base_dir, split="pretrain", train_prop=train_prop)
    learner = ContrastiveLearner(dataset, num_epochs, batch_size, log_dir)
    try:
        #Changed to evaluator_FINETUNE2.pt
        state_dict = torch.load(os.path.join(log_dir, 'evaluator_FINETUNE2.pt'))
        encoder = learner.get_model(256)
        teacher = SSL(encoder)
        teacher.load_state_dict(state_dict)
        teacher.eval()
    except FileNotFoundError:
        raise Exception("distillation requires teacher model")
    learner.distill(folds, task, label_file, log_file, augment, teacher, evaluator, learning_rate)


def test_(task, base_dir, log_dir, evaluator, seed=None, model_num=0):
    log_file = os.path.join(log_dir, f"test_log_{task}_{model_num}.txt")
    with open(log_file, "a+") as f:
        f.write(f"Seed: {seed}\n")
    label_file = os.path.join(base_dir, "processed", "{}_labels.csv".format(task))
    dataset = get_dataset(task, label_file, base_dir, split="test")
    learner = ContrastiveLearner(dataset, 0, 1, log_dir)
    try:
        state_dict = torch.load(os.path.join(log_dir, 'encoder.pth'))
        encoder = learner.get_model(256)
        encoder.load_state_dict(state_dict)
    except FileNotFoundError:
        encoder = None
    auc = learner.test(task, label_file, log_file, encoder, log_dir, evaluator_type=evaluator, model_num=model_num)
    auc_file = os.path.join(log_dir, f"auc_{task}_{model_num}.csv")
    with open(auc_file, "a") as f:
        f.write("{:.3f}\n".format(auc))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="train", choices={"pretrain", "train", "test", "distill"})
    parser.add_argument("--task", type=str, default=None,
                        choices={"disease", "demo", "wheeze", "crackle", "heartchallenge", "heart"})
    parser.add_argument("--log_dir", type=str, default=None)
    parser.add_argument("--data", type=str, default="../data")
    parser.add_argument("--evaluator", type=str, default=None, choices={"knn", "linear", "fine-tune", "cnn", "cnn-light", "distill-cnn"})
    parser.add_argument("--augment", type=str, default=None,
                        choices={"split", "raw", "spec", "spec+split", 'raw+split', 'time', 'freq', 'time+split'})
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--train_prop", type=float, default=1.0)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--full_data", default=False)
    parser.add_argument("--exp", type=int, default=None)
    parser.add_argument("--model_num", type=int, default=0)
    parser.add_argument("--restore", default=False)

    args = parser.parse_args()

    base_dir = os.path.join(os.getcwd(), args.data)
    log_dir = args.log_dir

    if args.mode == "pretrain":
        if log_dir is None:
            now = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            log_dir = os.path.join(base_dir, "logs", now)
        else:
            log_dir = os.path.join(base_dir, "logs", log_dir)
        print(f"Log Dir: {log_dir}")
        fi.make_path(log_dir)
        pretrain_(args.epochs, args.task, base_dir, log_dir, args.augment, args.train_prop, args.exp, args.restore)
    elif args.mode == "train":
        if log_dir is None:
            now = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            log_dir = os.path.join(base_dir, "logs", now)
        else:
            log_dir = os.path.join(base_dir, "logs", log_dir)
        print(f"Log Dir: {log_dir}")
        fi.make_path(log_dir)
        train_(args.epochs, args.task, base_dir, log_dir, args.evaluator, args.augment, args.folds, args.train_prop,
               args.full_data, args.model_num)
    elif args.mode == "distill":
        if log_dir is not None:
            log_dir = os.path.join(base_dir, "logs", log_dir)
        else:
            raise Exception("Distilling requires log dir")
        print(f"Log Dir: {log_dir}")
        fi.make_path(log_dir)
        distill_(args.epochs, args.task, base_dir, log_dir, args.evaluator, args.augment, args.folds, args.train_prop,
                 args.full_data)
    elif args.mode == "test":
        seed = 0
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        print("seed:", seed)
        if log_dir is not None:
            log_dir = os.path.join(base_dir, "logs", log_dir)
        else:
            raise Exception("Testing requires log dir")
        print(f"Log Dir: {log_dir}")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        test_(args.task, base_dir, log_dir, args.evaluator, seed, args.model_num)
