# Execute:  pyspark --packages com.databricks:spark-csv_2.10:1.3.0 --master local[*]

from pyspark import SparkContext
from pyspark.sql import SQLContext
from pyspark.sql.types import DoubleType, StringType
from pyspark.sql.functions import udf
import Preprocess as pp
from sklearn.model_selection import train_test_split

##############################
## Load and Preprocess Data ##
##############################
# Register all the functions in Preprocess.py with Spark Context
remove_stops_udf = udf(pp.remove_stops, StringType())
remove_features_udf = udf(pp.remove_features, StringType())
tag_and_remove_udf = udf(pp.tag_and_remove, StringType())
lemmatize_udf = udf(pp.lemmatize, StringType())

# create spark contexts
sqlContext = SQLContext(sc)

# Load data - label (0 = Bearish, 1 = Bullish)
data_df = sqlContext.read.load('file:///home/cloudera/Desktop/AAPL_FB_TSLA_2mo.csv', 
                          format='com.databricks.spark.csv', 
                          header='true', 
                          inferSchema='true')

data_df = data_df.where((data_df.label == 0.0) | (data_df.label == 1.0))

bearishCount = data_df.filter(data_df.label == 0.0).count()
bullishCount = data_df.filter(data_df.label == 1.0).count()
print("Total Bearish Tags = %g" % bearishCount)
print("Total Bullish Tags = %g" % bullishCount)

data_df.cache()
data_df.printSchema()

# remove stop words to reduce dimensionality
rm_stops_df = data_df.withColumn("stop_text", remove_stops_udf(data_df["Body"]))

# remove other non essential words, think of it as my personal stop word list
rm_features_df = rm_stops_df.withColumn("feat_text", remove_features_udf(rm_stops_df["stop_text"]))

# tag the words remaining and keep only Nouns, Verbs and Adjectives
tagged_df = rm_features_df.withColumn("tagged_text", tag_and_remove_udf(rm_features_df["feat_text"]))

# lemmatization of remaining words to reduce dimensionality & boost measures
lemm_df = tagged_df.withColumn("text", lemmatize_udf(tagged_df["tagged_text"]))

# dedupe important since alot of the tweets only differed by url's and RT mentions
dedup_df = lemm_df.dropDuplicates(['Body', 'label'])

# select only the columns we care about
cleanData_df = dedup_df.select(dedup_df['ID'], dedup_df['Date'], dedup_df['Symbol'], dedup_df['text'], dedup_df['label'])

bearishCount = cleanData_df.filter(cleanData_df.label == 0.0).count()
bullishCount = cleanData_df.filter(cleanData_df.label == 1.0).count()
print("Total Bearish Tags = %g" % bearishCount)
print("Total Bullish Tags = %g" % bullishCount)

# split training & validation sets with 70% to training
training, test = cleanData_df.randomSplit([0.70, 0.30], seed=1)

######################
## Spark ML Section ##
######################
from pyspark.ml.feature import HashingTF, IDF, Tokenizer
from pyspark.ml import Pipeline
from pyspark.ml.classification import NaiveBayes
from pyspark.ml.tuning import ParamGridBuilder, CrossValidator
from pyspark.ml.evaluation import MulticlassClassificationEvaluator

# Configure an ML pipeline, which consists of three stages: tokenizer, hashingTF, and nb.
tokenizer = Tokenizer(inputCol="text", outputCol="words")
hashingTF = HashingTF(inputCol=tokenizer.getOutputCol(), outputCol="features")
idf = IDF(inputCol="features", outputCol="idf")
nb = NaiveBayes(smoothing=1.0, modelType="multinomial")
pipeline = Pipeline(stages=[tokenizer, hashingTF, idf, nb])

paramGrid = ParamGridBuilder().addGrid(nb.smoothing, [0.0, 1.0]).addGrid(hashingTF.numFeatures, [100, 1000, 10000]).build()

cv = CrossValidator(estimator=pipeline, 
                    estimatorParamMaps=paramGrid, 
                    evaluator=MulticlassClassificationEvaluator(), 
                    numFolds=4)

cvModel = cv.fit(training).bestModel

result = cvModel.transform(test)
prediction_df = result.select("Date", "text", "label", "probability", "prediction")

bearish_df = prediction_df.filter(prediction_df['label']==0.0)
bearish_df.show(truncate=False)

bullish_df = prediction_df.filter(prediction_df['label']==1.0)
bullish_df.show(truncate=False)

# Compute test error
evaluator = MulticlassClassificationEvaluator(predictionCol="prediction")
print("Test Set Accuracy = %g" % evaluator.evaluate(result, {evaluator.metricName: "precision"}))


######################################################
## Write Output to Compute Aggregate for each Stock ##
######################################################

# Load full data set for given stock
data_df = sqlContext.read.load('file:///home/cloudera/Desktop/AAPL_FB_TSLA_2mo.csv', 
                          format='com.databricks.spark.csv', 
                          header='true', 
                          inferSchema='true')

data_df.cache()
data_df.printSchema()

# remove stop words to reduce dimensionality
rm_stops_df = data_df.withColumn("stop_text", remove_stops_udf(data_df["Body"]))
l
# remove other non essential words, think of it as my personal stop word list
rm_features_df = rm_stops_df.withColumn("feat_text", remove_features_udf(rm_stops_df["stop_text"]))

# tag the words remaining and keep only Nouns, Verbs and Adjectives
tagged_df = rm_features_df.withColumn("tagged_text", tag_and_remove_udf(rm_features_df["feat_text"]))

# lemmatization of remaining words to reduce dimensionality & boost measures
lemm_df = tagged_df.withColumn("text", lemmatize_udf(tagged_df["tagged_text"]))

# dedupe important since alot of the tweets only differed by url's and RT mentions
dedup_df = lemm_df.dropDuplicates(['Body', 'label'])

# select only the columns we care about
cleanData_df = dedup_df.select(dedup_df['CreateTime'], dedup_df['Date'], dedup_df['Symbol'], dedup_df['text'], dedup_df['label'])

# Predict on full data set using the trained cross-validated best NB model
result = cvModel.transform(cleanData_df)
prediction_df = result.select("CreateTime", "Symbol", "label", "probability", "prediction")

# Write data to csv for post-processing in Excel
export_path = '/home/cloudera/Desktop/nb_intraday_aggregate.csv'
prediction_pd = prediction_df.toPandas()
prediction_pd[["CreateTime", "probability", "Symbol"]].to_csv(export_path, index=False)


###########################
## Plot Confusion Matrix ##
###########################
import itertools
import numpy as np
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix

def plot_confusion_matrix(cm, classes,
                          normalize=True,
                          title='Confusion matrix',
                          cmap=plt.cm.Blues):
    """
    This function prints and plots the confusion matrix.
    Normalization can be applied by setting `normalize=True`.
    """
    plt.imshow(cm, interpolation='nearest', cmap=cmap)
    plt.title(title)
    plt.colorbar()
    tick_marks = np.arange(len(classes))
    plt.xticks(tick_marks, classes, rotation=45)
    plt.yticks(tick_marks, classes)
    if normalize:
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        print("Normalized confusion matrix")
    else:
        print('Confusion matrix, without normalization')
    print(cm)
    thresh = cm.max() / 2.
    for i, j in itertools.product(range(cm.shape[0]), range(cm.shape[1])):
        plt.text(j, i, cm[i, j],
                 horizontalalignment="center",
                 color="white" if cm[i, j] > thresh else "black")
    plt.tight_layout()
    plt.ylabel('True label')
    plt.xlabel('Predicted label')

# Convert Dataframe to Numpy Array
testLabels = np.array(test.select('label').collect())
testPreds = np.array(prediction_df.select('prediction').collect())

# Compute confusion matrix
cnf_matrix = confusion_matrix(testLabels, testPreds)
print(cnf_matrix.astype('float') / cnf_matrix.sum(axis=1)[:, np.newaxis])
np.set_printoptions(precision=2)

# Plot confusion matrix
plt.figure()
plot_confusion_matrix(cnf_matrix, classes=['Bearish','Bullish'], title='Confusion Matrix')

plt.show()