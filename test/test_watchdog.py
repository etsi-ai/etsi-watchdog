# // this library handles tabular data 
import pandas as pd 
import csv 
#this library is used to read and write CSV files
from etsi.watchdog import DriftMonitor 
#  done to import the DriftMonitor class from etsi.watchdog module (this tool will detect the data drift)

X_ref = pd.DataFrame({  
#  here we define the refernce dataset with some features that we will use to monitor the drift)
    "feature1": [1, 2, 2, 3, 4, 5],
    "feature2": [100, 102, 101, 98, 97, 99],
    "category": ["A", "B", "A", "C", "B", "A"]
})

X_live = pd.DataFrame({
    # this will be our incoming or live dataset that we will monitor to detect drift 
    "feature1": [11, 12, 13, 14, 15, 16],
    "feature2": [188, 189, 190, 191, 192, 193],
    "category": ["A", "C", "C", "C", "D", "D"]
})
# this is the main code that will display the following message when drift is detected 
def alert():
    print(" Drift detected! Take action!")

monitor = DriftMonitor(reference=X_ref) 
#here in the code above we have given the model some refernce data so that it can learn from it and then check our main live data that we wanna monitor 
result = monitor.watch(X_live, threshold=0.2)
# monitor.watch is the method or function that will check the live data against the refernce data for 
# eg.If any feature's PSI (Population Stability Index) score is above 0.2, it considers it as drift because we have set the threshold as 0.2 

#so if  < 0.1 = no drift

# and if 0.1 to 0.2 = slight drift

# else if 0.2 >= major drif
print("PSI Scores:", result.psi)
# print the PSI scores for each feature in the live dataset
result.on_drift(alert)


result.psi.to_csv("drift_report.csv")
#this will save the PSI scores to a CSV file named "drift_report.csv" for analysis and reporting 
for feature, psi_score in result.psi.items():
    #this loop will iterate through each feature and it will predict the drift based on the PSI score 
    if psi_score < 0.1:
        status = "No Drift"
        
    elif psi_score < 0.2:
        status = " Moderate Drift"
    else:
        status = "Significant Drift"
        #these statements will check the drift and print messages like no drift , moderate drift or significant drift"
    print(f"Feature: {feature} — PSI: {psi_score:.2f} {status}")