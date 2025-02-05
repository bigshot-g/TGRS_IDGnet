import scipy.io as scio
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import time
import torch.utils.data as Data
from Model import Model
from sklearn.metrics import confusion_matrix, accuracy_score, cohen_kappa_score
from Utils import aa_and_each_accuracy, set_seed
from thop import profile, clever_format
from Domain_expanison import data_process
from Loss import LossRMSE, SubConLoss


S = 'source dataset name' #Replace the dataset name
T = 'target dataset name' #Replace the dataset name
cls = 6  # the number of class
hsi_channel = 126
lidar_channel = 1
patch_size = 11
batch_size_TR = 16
batch_size_TE = 2000

train_HSI, train_LiDAR, label_train, test_HSI, test_LiDAR, label_test, SEED, train_HSI_ex, train_LiDAR_ex = data_process(S, T, cls)

print("Data convertng...")
start_time = time.time()

train_HSI = torch.from_numpy(np.array(train_HSI))
train_LiDAR = torch.from_numpy(np.array(train_LiDAR))
train_HSI_ex = torch.from_numpy(np.array(train_HSI_ex))
train_LiDAR_ex = torch.from_numpy(np.array(train_LiDAR_ex))
label_train = torch.from_numpy(np.array(label_train)).long()
label_train = label_train.view(-1, 1)

test_HSI = torch.from_numpy(np.array(test_HSI))
test_LiDAR = torch.from_numpy(np.array(test_LiDAR))
label_test = torch.from_numpy(np.array(label_test)).long()
label_test = label_test.view(-1, 1)

print("End data convert:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), "耗时%.3fs" % (time.time() - start_time))

set_seed(SEED)

train_data = Data.TensorDataset(train_HSI, torch.unsqueeze(train_LiDAR, dim=3), label_train, train_HSI_ex, torch.unsqueeze(train_LiDAR_ex, dim=3))
test_data = Data.TensorDataset(test_HSI, torch.unsqueeze(test_LiDAR, dim=3), label_test)
train_iter = Data.DataLoader(
        dataset=train_data,
        batch_size=batch_size_TR,
        shuffle=True,
        num_workers=0,
    )
test_iter = Data.DataLoader(
        dataset=test_data,
        batch_size=batch_size_TE,
        shuffle=False,
        num_workers=0,
    )

net = Model(hsi_channel, lidar_channel, cls)
criterion_cls = nn.CrossEntropyLoss()
opt = torch.optim.Adam(net.parameters(), lr=0.00001, betas=(0.9, 0.999))  # 0.0001-0.000001
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 4, eta_min=0.000005)

print("Start Traning...")
start_time_Tr = time.time()
net.cuda()
for epoch in range(70):
    print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())))
    net.train()
    for i, (H, L, y, H_ex, L_ex) in enumerate(train_iter):
        H = H.cuda()
        L = L.cuda()
        y = y.cuda()
        H_ex = H_ex.cuda()
        L_ex = L_ex.cuda()
        opt.zero_grad()
        result_HL, result_HexLex, F_HL, F_HL_ex, F_con = net(H, L, H_ex, L_ex)
        # loss calculation
        # pull loss
        loss_pull = LossRMSE()(F_HL, F_HL_ex)
        # cls loss
        loss_clsHL = nn.CrossEntropyLoss()(result_HL, y.squeeze(dim=1))
        loss_clsHexLex = nn.CrossEntropyLoss()(result_HexLex, y.squeeze(dim=1))
        # con loss
        label = torch.cat([y, y], dim=0)
        loss_con = SubConLoss()(F_con, label)
        loss = loss_clsHL + loss_clsHexLex + loss_pull*10 + loss_con*4   # p0.5 c4
        loss.backward()
        opt.step()
        print('[%d, %5d] loss:%.3f, loss_clsHL:%.3f, loss_clsHexLex:%.3f, loss_pull:%.3f, loss_con:%.3f'\
                % (epoch + 1, (i + 1) * batch_size_TR, loss, loss_clsHL, loss_clsHexLex, loss_pull, loss_con))
    print("The learning rate of epoch %d：G:%f" % (epoch+1, opt.param_groups[0]['lr']))
print("End training:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())), "Elapsed time%.3fs" % (time.time() - start_time_Tr))
print('Parameter total:', sum(p.numel() for p in net.parameters()))
torch.save(net.state_dict(), './test.pth')

net.load_state_dict(torch.load('test.pth'))
print("Start testing...")
#net.cuda()
net.eval()
start_time_Te = time.time()
correct = 0
total = len(label_test)
if hasattr(torch.cuda, 'empty_cache'):
    torch.cuda.empty_cache()
pre = np.array([], dtype=np.int32)
for i, (H, L, y) in enumerate(test_iter):
    H = H.cuda()
    L = L.cuda()
    y = y.cuda()
    with torch.no_grad():
        result_HL, result_HexLex, _, _, _ = net(H, L, H, L)
    _, predicted = torch.max(result_HL.data, 1)
    cou = 0
    cou += (predicted == y.squeeze(dim=1)).sum()
    correct += cou
    predicted = np.array(predicted.cpu())
    pre = np.concatenate([pre, predicted], 0)
    print("[%d, %5d] accuracy:%.4f %%" % ((i+1)*batch_size_TE, total, 100 * (correct.float() / ((i + 1)*batch_size_TE))))
print("OA：%.4f %%" % (100*correct.float()/total))
print("End test,Elapsed time%.3fs" % (time.time()-start_time_Te))
print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())))
print("SEED:", SEED)

label = label_test
pre = np.asarray(pre, dtype=np.int64)
confusion_matrix = confusion_matrix(label, pre)
print(confusion_matrix)
oa = accuracy_score(label, pre)
print("OA:", oa)
each_acc, aa = aa_and_each_accuracy(confusion_matrix)
print(each_acc)
print("AA:", aa)
kappa = cohen_kappa_score(label, pre)
print("Kappa:", kappa)
np.savetxt("confusion_matrix", confusion_matrix, fmt="%5d")
scio.savemat('label_pre.mat', {'label': pre})
input1 = torch.randn(1, patch_size, patch_size, hsi_channel).cuda()
input2 = torch.randn(1, patch_size, patch_size, lidar_channel).cuda()
input3 = torch.randn(1, patch_size, patch_size, hsi_channel).cuda()
input4 = torch.randn(1, patch_size, patch_size, lidar_channel).cuda()
flops, params = profile(net, inputs=(input1, input2, input1, input2))
flops, params = clever_format([flops, params], "%.3f")
print("FLOPs:", flops, "Parameters:", params)
print("All elapsed time", time.time() - start_time, "s")
