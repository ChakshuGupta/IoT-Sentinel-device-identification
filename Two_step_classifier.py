import os
import fnmatch
import pyshark
import numpy as np
import pickle
import random
from random import randint
from scapy.all import *
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from pyxdameraulevenshtein import damerau_levenshtein_distance, normalized_damerau_levenshtein_distance
from sklearn import svm
from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix
from sklearn.metrics import log_loss

#import feature_extraction as fe
import features_scapy as fe

dest_ip_set = {}    # stores the destination IP set, a global variable
dst_ip_counter = 0  # keeps destination counter value, a global variable
last_vector = []    # for the comparison of consecutive identical packets
capture_len = 0
feature_set = []
prev_class = ""
concat_feature = []
count = 0
source_mac_add = ""


def pcap_class_generator(folder):
    for path, dir_list, file_list in os.walk(folder):
        for name in fnmatch.filter(file_list, "*.pcap"):
            global dst_ip_counter
            global dest_ip_set
            dest_ip_set.clear()  # stores the destination IP set
            dst_ip_counter = 0
            global feature_set
            global prev_class
            global concat_feature
            print(os.path.join(path, name))
            prev_class = ""
            concat_feature = []
            feature_set = []
            yield os.path.join(path, name), os.path.basename(os.path.dirname(path)), os.path.basename(os.path.normpath(path))


def packet_class_generator(pcap_class_gen):
    for pcapfile, vendor_, class_ in pcap_class_gen:
        capture = rdpcap(pcapfile)
        global capture_len
        global source_mac_add
        global count
        count = 0
        capture_len = 0
        mac_address_list = {}
        src_mac_address_list = {}

        for i, (packet) in enumerate(capture):
            if packet[0].src not in mac_address_list:  # Counting the source MAC counter value
                mac_address_list[packet[0].src] = 1
            else:
                mac_address_list[packet[0].src] += 1

            if packet[0].dst not in mac_address_list:  # Counting the Destination MAC counter value
                mac_address_list[packet[0].dst] = 1
            else:
                mac_address_list[packet[0].dst] += 1

            if packet[0].src not in src_mac_address_list:  # keeping the source MAC address counter for capture length
                src_mac_address_list[packet[0].src] = 1
            else:
                src_mac_address_list[packet[0].src] += 1

        highest = max(mac_address_list.values())
        for k, v in mac_address_list.items():
            if v == highest:
                if k in src_mac_address_list:
                    source_mac_add = k
        capture_len = src_mac_address_list[source_mac_add]

        for i, (packet) in enumerate(capture):
            if packet[0].src == source_mac_add:
                yield packet, vendor_, class_


def feature_class_generator(packet_class_gen):
    for packet, vendor_, class_ in packet_class_gen:
        global dst_ip_counter
        global dest_ip_set
        global last_vector
        global count
        count = count + 1

        #  0   1    2   3       4      5     6    7    8      9     10    11     12   13   14    15     16         17         18         19             20                21         22
        #ARP |LLC |IP |ICMP |ICMPv6 |EAPoL |TCP |UDP |HTTP |HTTPS |DHCP |BOOTP |SSDP |DNS |MDNS |NTP |padding |RouterAlert |size(int) |rawData |dst_ip_counter(int) |src_pc(int) |dst_pc(int)
        fvector = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

        nl_pro = "None"     # stores network layer protocol
        tl_pro = "None"     # stores transport layer protocol

        fvector[18] = fe.get_length_feature(packet) # Packet length
        fvector[1] = fe.get_LLC_feature(packet)     # check for LLC layer header
        fvector[16] = fe.get_padding_feature(packet)   # check for padding layer header
        fvector[0] = fe.get_arp_feature(packet)     # ARP feature
        fvector[2], nl_pro = fe.get_ip_feature(packet)      # IP feature
        fvector[5] = fe.get_eapol_feature(packet)   # EAPoL feature
        fvector[19] = fe.get_rawdata_feature(packet)    # RawData feature

        if nl_pro == "IP":      # Inspecting the IP layer
            fvector[3], fvector[4] = fe.get_icmp_feature(packet)    # ICMP, ICMPv6 features
            fvector[6], fvector[7], tl_pro = fe.get_tcpudp_feature(packet)  # TCP, UDP features
            fvector[17] = fe.get_r_alert_feature(packet)            # Router Alert feature
            fvector[20], dest_ip_set, dst_ip_counter = fe.get_dest_ip_counter_feature(packet, dest_ip_set, dst_ip_counter)    # Destination ip counter feature

        if tl_pro == "TCP" or tl_pro == "UDP":
            fvector[13] = fe.get_dns_feature(packet, tl_pro)    # DNS feature
            fvector[10], fvector[11] = fe.get_bootp_dhcp_feature(packet, tl_pro)    # DHCP and BOOTP features
            fvector[8] = fe.get_http_feature(packet, tl_pro)    # HTTP feature
            fvector[15] = fe.get_ntp_feature(packet, tl_pro)    # NTP feature
            fvector[9] = fe.get_https_feature(packet, tl_pro)   # HTTPS feature
            fvector[12] = fe.get_ssdp_feature(packet, tl_pro)   # SSDP feature
            fvector[14] = fe.get_mdns_feature(packet, tl_pro)   # MDNS feature
            fvector[21] = fe.get_srcpc_feature(packet, tl_pro)  # source port class feature
            fvector[22] = fe.get_dstpc_feature(packet, tl_pro)  # destination port class feature

        yield fvector, vendor_, class_

features_DL = {}
all_features_DL = {}
f_array = []


def dataset(feature_class_gen):
    global feature_set
    global prev_class
    global concat_feature
    global capture_len
    global count
    global f_array

    def g():
        global feature_set
        global prev_class
        global concat_feature
        global capture_len
        global count
        global f_array
        global last_vector

        for i, (feature, vendor_, class_) in enumerate(feature_class_gen):
            # This block removes the consecutive identical features from the data set
            if not last_vector:
                last_vector = feature
            else:
                if all(i == j for i, j in zip(last_vector, feature)):
                    if capture_len == count and len(concat_feature) < 276:  # if the number of feature count is < 276,
                        while len(concat_feature) < 276:  # add 0's as padding
                            concat_feature = concat_feature + [0]
                        yield concat_feature, vendor_, class_
                        print("capture_len == count", concat_feature)
                    continue
                last_vector = feature

            # Generating the F' vector from F matrix
            if (len(feature_set) < 12) or (prev_class != class_):       # Get 12 unique features for each device type
                if not prev_class:                                      # concatenated into a 276 dimensional vector
                    prev_class = class_
                    feature_set.append(feature)
                    concat_feature = concat_feature + feature
                else:
                    if prev_class is class_:
                        if not feature in feature_set:  # Adding a unique feature
                            feature_set.append(feature)
                            concat_feature = concat_feature + feature
                            if len(feature_set) == 12:
                                yield concat_feature, vendor_, class_
                                print("len(feature_set) == 12", concat_feature)
                    else:
                        prev_class = ""
                        feature_set = []
                        concat_feature = []
                        feature_set.append(feature)
                        concat_feature = concat_feature + feature

            if capture_len == count and len(concat_feature) < 276:  # if the number of feature count is < 276,
                while len(concat_feature) < 276:                    # add 0's as padding
                    concat_feature = concat_feature + [0]
                yield concat_feature, vendor_, class_
                print("capture_len == count", concat_feature)
    return zip(*g())


def load_data(pcap_folder_name):
    pcap_gen = pcap_class_generator(pcap_folder_name)
    packet_gen = packet_class_generator(pcap_gen)
    feature_gen = feature_class_generator(packet_gen)
    dataset_X, dataset_v, dataset_y = dataset(feature_gen)
    dataset_X = np.array(dataset_X)
    dataset_v = np.array(dataset_v)
    dataset_y = np.array(dataset_y)
    return dataset_X, dataset_v, dataset_y


pcap_folder = "F:\\MSC\\Master Thesis\\Network traces\\captures_IoT_Sentinel\\Vendor_based"

try:
    dataset_X = pickle.load(open("vendor_dataset_X.pickle", "rb"))
    dataset_v = pickle.load(open("vendor_dataset_v.pickle", "rb"))
    dataset_y = pickle.load(open("vendor_dataset_y.pickle", "rb"))
    print("Pickling successful......")
except (OSError, IOError) as e:
    print("No pickle datasets are available....")
    dataset_X, dataset_v, dataset_y = load_data(pcap_folder)
    pickle.dump(dataset_X, open("vendor_dataset_X.pickle", "wb"))
    pickle.dump(dataset_v, open("vendor_dataset_v.pickle", "wb"))
    pickle.dump(dataset_y, open("vendor_dataset_y.pickle", "wb"))
    all_features_DL = features_DL
    features_DL = {}

X_train, X_test, v_train, v_test, y_train, y_test = train_test_split(dataset_X, dataset_v, dataset_y,
                                                                     test_size=0, random_state=0)

clf = RandomForestClassifier(n_estimators=10)
clf.fit(X_train, v_train)

test_folder="F:\\MSC\\Master Thesis\\Network traces\\captures_IoT_Sentinel\\not trained data"
X_unknown, v_unknown, y_unknown = load_data(test_folder)
X_unknown = np.array(X_unknown)
y_unknown = np.array(y_unknown)

v_predict = clf.predict(X_unknown)
print("Tested vendor: ", v_test)
print("Predicted vendor: ", v_predict)

for i, (pre_vendor) in enumerate(v_predict):
    data_X = dataset_X[dataset_v == pre_vendor]
    data_y = dataset_y[dataset_v == pre_vendor]

    X_train, X_test, y_train, y_test = train_test_split(data_X, data_y, test_size=0, random_state=0)
    clf2 = RandomForestClassifier(n_estimators=10)
    clf2.fit(X_train, y_train)

    unknown_fp = []
    unknown_fp.append(X_unknown[i])
    dev_predict = clf2.predict(unknown_fp)
    print("Predicted device: ", dev_predict, "Predicted vendor: ", pre_vendor, "Actual device: ", y_unknown[i])