"""
Code for the CRAR agent using Keras

"""

import numpy as np
np.set_printoptions(threshold=np.nan)
from keras.optimizers import SGD,RMSprop
from keras import backend as K
from ..base_classes import LearningAlgo
from .NN_CRAR_keras import NN # Default Neural network used
import tensorflow as tf
config = tf.ConfigProto()
config.gpu_options.allow_growth=True
sess = tf.Session(config=config)
import copy

def mean_squared_error_p(y_true, y_pred):
    return K.clip(K.max(  K.square( y_pred - y_true )  ,  axis=-1  )-1,0.,100.)   # = mse error
    #return K.clip(K.mean(  K.square( y_pred - y_true )  ,  axis=-1  )-1,0.,100.)   # = mse error
    #return K.mean(  K.square( K.clip(K.abs(y_pred - y_true)-1,0.,100.) )  ,  axis=-1  )   # = mse error

def exp_dec_error(y_true, y_pred):
    return K.exp( - 5.*K.sqrt( K.clip(K.sum(K.square(y_pred), axis=-1, keepdims=True),0.000001,10) )  ) # tend to increase y_pred

def cosine_proximity2(y_true, y_pred):
    y_true = K.l2_normalize(y_true[:,0:2], axis=-1)
    y_pred = K.l2_normalize(y_pred[:,0:2], axis=-1)
    return -K.sum(y_true * y_pred, axis=-1)

#def rms_from_squared_components(y_true, y_pred):
#    return - K.sum(  K.sqrt( K.clip(y_pred,0.000001,1))  , axis=-1, keepdims=True ) # tend to increase y_pred --> loss -1#
#
#def squared_error_from_squared_components(y_true, y_pred):
#    return - K.sum(  K.clip(y_pred,0.,1)  , axis=-1, keepdims=True ) # tend to increase y_pred --> loss -1

def loss_diff_s_s_(y_true, y_pred):
    return K.square(   1.    -    K.sqrt(  K.clip( K.sum(y_pred,axis=-1,keepdims=True), 0.000001 , 1. )  )     ) # tend to increase y_pred --> loss -1

class CRAR(LearningAlgo):
    """
    Combined Reinforcement learning via Abstract Representations (CRAR) using Keras
    
    Parameters
    -----------
    environment : object from class Environment
    rho : float
        Parameter for rmsprop. Default : 0.9
    rms_epsilon : float
        Parameter for rmsprop. Default : 0.0001
    momentum : float
        Default : 0
    clip_delta : float
        Not implemented.
    freeze_interval : int
        Period during which the target network is freezed and after which the target network is updated. Default : 1000
    batch_size : int
        Number of tuples taken into account for each iteration of gradient descent. Default : 32
    update_rule: str
        {sgd,rmsprop}. Default : rmsprop
    random_state : numpy random number generator
    double_Q : bool, optional
        Activate or not the double_Q learning.
        More informations in : Hado van Hasselt et al. (2015) - Deep Reinforcement Learning with Double Q-learning.
    neural_network : object, optional
        default is deer.qnetworks.NN_keras
    """

    def __init__(self, environment, rho=0.9, rms_epsilon=0.0001, momentum=0, clip_delta=0, freeze_interval=1000, batch_size=32, update_rule="rmsprop", random_state=np.random.RandomState(), double_Q=False, neural_network=NN, **kwargs):
        """ Initialize environment
        
        """
        LearningAlgo.__init__(self,environment, batch_size)

        self._rho = rho
        self._rms_epsilon = rms_epsilon
        self._momentum = momentum
        self._update_rule = update_rule
        self._freeze_interval = freeze_interval
        self._double_Q = double_Q
        self._random_state = random_state
        self.update_counter = 0    
        self._high_int_dim = kwargs.get('high_int_dim',False)
        self._internal_dim = kwargs.get('internal_dim',2)
        self.loss_interpret=0
        self.loss_T=0
        self.loss_T2=0
        self.loss_disentangle_t=0
        self.loss_disentangle_a=0
        self.lossR=0
        self.loss_Q=0
        self.loss_disambiguate1=0
        self.loss_disambiguate2=0
        self.loss_gamma=0

        
        self.learn_and_plan = neural_network(self._batch_size, self._input_dimensions, self._n_actions, self._random_state, high_int_dim=self._high_int_dim, internal_dim=self._internal_dim)

        self.encoder = self.learn_and_plan.encoder_model()
        self.encoder_diff = self.learn_and_plan.encoder_diff_model(self.encoder)
        
        self.R = self.learn_and_plan.R_model()
        self.Q = self.learn_and_plan.Q_model()
        self.gamma = self.learn_and_plan.R_model()
        self.transition = self.learn_and_plan.transition_model()
#        self.transition2 = self.learn_and_plan.transition_model2()

        self.full_Q=self.learn_and_plan.full_Q_model(self.encoder,self.Q,0,self._df)
        
        # used to fit rewards
        self.full_R = self.learn_and_plan.full_R_model(self.encoder,self.R)
        
        # used to fit gamma
        self.full_gamma = self.learn_and_plan.full_R_model(self.encoder,self.gamma)
        
        # used to fit transitions
        self.diff_Tx_x_ = self.learn_and_plan.diff_Tx_x_(self.encoder,self.transition)#full_transition_model(self.encoder,self.transition)
        
        # used to force features variations
        if(self._high_int_dim==False):
            self.force_features=self.learn_and_plan.force_features(self.encoder,self.transition)
                
        # constraint on consecutive t
        self.diff_s_s_ = self.learn_and_plan.encoder_diff_model(self.encoder)
#        self.diff_Tx = self.learn_and_plan.diff_Tx(self.transition)

        # used to disentangle actions
        self.diff_sa_sa = self.learn_and_plan.diff_sa_sa(self.encoder,self.transition)
                
        layers=self.encoder.layers+self.Q.layers+self.R.layers+self.gamma.layers+self.transition.layers
        # Grab all the parameters together.
        self.params = [ param
                    for layer in layers 
                    for param in layer.trainable_weights ]

        self._compile()

        self.learn_and_plan_target = neural_network(self._batch_size, self._input_dimensions, self._n_actions, self._random_state, high_int_dim=self._high_int_dim, internal_dim=self._internal_dim)
        self.encoder_target = self.learn_and_plan_target.encoder_model()
        self.Q_target = self.learn_and_plan_target.Q_model()
        self.R_target = self.learn_and_plan_target.R_model()
        self.gamma_target = self.learn_and_plan_target.R_model()
        self.transition_target = self.learn_and_plan_target.transition_model()

        self.full_Q_target = self.learn_and_plan_target.full_Q_model(self.encoder,self.Q) # FIXME
        self.full_Q_target.compile(optimizer='rmsprop', loss='mse') #The parameters do not matter since training is done on self.full_Q

        layers=self.encoder_target.layers+self.Q_target.layers+self.R_target.layers+self.gamma_target.layers+self.transition_target.layers
        # Grab all the parameters together.
        self.params_target = [ param
                    for layer in layers 
                    for param in layer.trainable_weights ]

        self._resetQHat()

    def getAllParams(self):
        params_value=[]
        for i,p in enumerate(self.params):
            params_value.append(K.get_value(p))
        return params_value

    def setAllParams(self, list_of_values):
        for i,p in enumerate(self.params):
            K.set_value(p,list_of_values[i])

    def train(self, states_val, actions_val, rewards_val, next_states_val, terminals_val):
        """
        Train one batch.

        1. Set shared variable in states_shared, next_states_shared, actions_shared, rewards_shared, terminals_shared         
        2. perform batch training

        Parameters
        -----------
        states_val : list of batch_size * [list of max_num_elements* [list of k * [element 2D,1D or scalar]])
        actions_val : b x 1 numpy array of integers
        rewards_val : b x 1 numpy array
        next_states_val : list of batch_size * [list of max_num_elements* [list of k * [element 2D,1D or scalar]])
        terminals_val : b x 1 numpy boolean array

        Returns
        -------
        Average loss of the batch training (RMSE)
        Individual (square) losses for each tuple
        """
        
        onehot_actions = np.zeros((self._batch_size, self._n_actions))
        onehot_actions[np.arange(self._batch_size), actions_val[:,0]] = 1
        onehot_actions_rand = np.zeros((self._batch_size, self._n_actions))
        onehot_actions_rand[np.arange(self._batch_size), np.random.randint(0,2,(32))] = 1
        states_val=list(states_val)
        next_states_val=list(next_states_val)
        #for i,o in enumerate(states_val):
        #    if(o.ndim==5): #FIXME
        #        states_val[i]=states_val[i][:,0,:,:,:]/128.-1
        #for i,o in enumerate(next_states_val):
        #    if(o.ndim==5): #FIXME
        #        next_states_val[i]=next_states_val[i][:,0,:,:,:]/128.-1
            
        Es_=self.encoder.predict(next_states_val)
        Es=self.encoder.predict(states_val)
        ETs=self.transition.predict([Es,onehot_actions])
        R=self.R.predict([Es,onehot_actions])
                   
        if(self.update_counter%500==0):
            print states_val[0][0]
            print "len(states_val)"
            print len(states_val)
            print "next_states_val[0][0]"
            print next_states_val[0][0]
            print "actions_val[0], rewards_val[0], terminals_val[0]"
            print actions_val[0], rewards_val[0], terminals_val[0]
            print "Es[0],ETs[0],Es_[0]"
            if(Es.ndim==4):
                print np.transpose(Es, (0, 3, 1, 2))[0],np.transpose(ETs, (0, 3, 1, 2))[0],np.transpose(Es_, (0, 3, 1, 2))[0]    # data_format='channels_last' --> 'channels_first'
            else:
                print Es[0],ETs[0],Es_[0]
            print "R[0]"
            print R[0]
            
        # Fit transition
        #print "states_val+next_states_val+[onehot_actions]+[(1-terminals_val)]"
        #print states_val+next_states_val+[onehot_actions]+[(1-terminals_val)]
        l=self.diff_Tx_x_.train_on_batch(states_val+next_states_val+[onehot_actions]+[(1-terminals_val)], np.zeros_like(Es)) #np.zeros((self._batch_size,self.learn_and_plan.internal_dim))
        #print "l"
        #print l
        self.loss_T+=l
        
        # Interpretable AI
        if(self._high_int_dim==False):
            target_modif_features=np.zeros((self._n_actions,self._internal_dim))
            ## Catcher
            #target_modif_features[0,0]=1    # dir
            #target_modif_features[1,0]=-1   # opposite dir
            #target_modif_features[0:2,1]=1    # temps
            ## Laby
            target_modif_features[0,0]=1
            target_modif_features[1,0]=0
            #target_modif_features[2,1]=0
            #target_modif_features[3,1]=0
            target_modif_features=np.repeat(target_modif_features,self._batch_size,axis=0)
            states_val_tiled=[]
            for obs in states_val:
                states_val_tiled.append(np.tile(obs,(self._n_actions,1,1,1)))
            onehot_actions_tiled = np.diag(np.ones(self._n_actions))#np.zeros((self._batch_size*self._n_actions, self._n_actions))
            onehot_actions_tiled = np.repeat(onehot_actions_tiled,self._batch_size,axis=0)
                
            self.loss_interpret+=self.force_features.train_on_batch(states_val_tiled+[onehot_actions_tiled], target_modif_features)

    
        # Fit rewards
        self.lossR+=self.full_R.train_on_batch(states_val+[onehot_actions], rewards_val) 

        # Fit gammas
        self.loss_gamma+=self.full_gamma.train_on_batch(states_val+[onehot_actions], (1-terminals_val[:])*self._df)

        # Loss to ensure entropy but limited volume in abstract state space, avg=0 and sigma=1
        # reduce the squared value of the abstract features
        #print "states_val[0][0:2]"
        #print states_val[0][0:2]
        #print "self.encoder.predict(states_val)"
        #print self.encoder.predict(states_val)
        l=self.encoder.train_on_batch(states_val,np.zeros_like(Es)) #np.zeros((self._batch_size,self.learn_and_plan.internal_dim)))
        #print l
        self.loss_disambiguate1+=l
        
        # Increase the entropy in the abstract features of two states
        # This is done only when states_val is made up of only one observation --> FIXME
        rolled=np.roll(states_val[0],1,axis=0)
#        for i in range(self._batch_size):
#            j=0
#            l=0
#            while((states_val[0][i]==rolled[i+j-l]).all()):
#                if(i+j==31):
#                    l=self._batch_size
#                if(j==31):
#                    break
#                j=j+1
#            rolled[i]=rolled[i+j-l]
        self.loss_disambiguate2+=self.encoder_diff.train_on_batch([states_val[0],rolled],np.reshape(np.zeros_like(Es),(self._batch_size,-1))) #np.zeros((self._batch_size,self.learn_and_plan.internal_dim)))


        self.loss_disentangle_t+=self.diff_s_s_.train_on_batch(states_val+next_states_val, np.reshape(np.zeros_like(Es),(self._batch_size,-1)))#np.ones(self._batch_size)) #np.ones((self._batch_size,3))*2) 

        # Disentangle actions
        self.loss_disentangle_a+=self.diff_sa_sa.train_on_batch(states_val+[onehot_actions,onehot_actions_rand], np.reshape(np.zeros_like(Es),(self._batch_size,-1)))#np.ones(self._batch_size))

#
#        # Loss to have all s' following s,a with a to a distance 1 of s,a)
#        tiled_x=np.tile(Es,(self._n_actions,1))
#        tiled_onehot_actions=np.tile(onehot_actions,(self._n_actions,1))
#        tiled_onehot_actions2=np.repeat(np.diag(np.ones(self._n_actions)),self._batch_size,axis=0)
#        #self.loss_disentangle_a+=self.diff_Tx.train_on_batch([tiled_x,tiled_onehot_actions,tiled_x,tiled_onehot_actions2], np.ones(self._batch_size*self._n_actions)) 


        
        if(self.update_counter%500==0):
            print "self.loss_Q"
            print self.loss_Q
            print "self.loss_T/100.,self.lossR/100.,self.loss_gamma/100.,self.loss_Q/100.,self.loss_disentangle_t/100.,self.loss_disentangle_a/100.,self.loss_disambiguate1/100.,self.loss_disambiguate2/100."
            print self.loss_T/100.,self.lossR/100.,self.loss_gamma/100.,self.loss_Q/100.,self.loss_disentangle_t/100.,self.loss_disentangle_a/100.,self.loss_disambiguate1/100.,self.loss_disambiguate2/100.
            
            if(self._high_int_dim==False):
                print "self.loss_interpret/100."
                print self.loss_interpret/100.

            self.lossR=0
            self.loss_gamma=0
            self.loss_Q=0
            self.loss_T=0
            self.loss_interpret=0

            self.loss_disentangle_t=0
            self.loss_disentangle_a=0
            
            self.loss_disambiguate1=0
            self.loss_disambiguate2=0
            
            print "self.encoder.train_on_batch([states_val[0]],np.zeros((32,self.learn_and_plan.internal_dim)))"
            print self.encoder.train_on_batch([states_val[0]],np.zeros_like(Es)) #np.zeros((32,self.learn_and_plan.internal_dim)))
            print self.encoder.train_on_batch([states_val[0]],np.zeros_like(Es)) #np.zeros((32,self.learn_and_plan.internal_dim)))

            print "self.encoder_diff.train_on_batch([states_val[0],np.roll(states_val[0],1,axis=0)],np.zeros((32,self.learn_and_plan.internal_dim)))"
            print self.encoder_diff.train_on_batch([states_val[0],rolled],np.reshape(np.zeros_like(Es),(self._batch_size,-1))) #np.zeros((32,self.learn_and_plan.internal_dim)))
            print self.encoder_diff.train_on_batch([states_val[0],rolled],np.reshape(np.zeros_like(Es),(self._batch_size,-1))) #np.zeros((32,self.learn_and_plan.internal_dim)))

            print "self.encoder.train_on_batch([states_val[0]],np.zeros((32,self.learn_and_plan.internal_dim)))"
            print self.encoder.train_on_batch([states_val[0]],np.zeros_like(Es)) #np.zeros((32,self.learn_and_plan.internal_dim)))


        if self.update_counter % self._freeze_interval == 0:
            self._resetQHat()
        
        #next_q_vals = self.next_full_Q.predict([next_states_val[0],np.zeros_like(Es)]) #np.zeros((32,self.learn_and_plan.internal_dim))])
        next_q_vals = self.full_Q_target.predict(next_states_val)
        
        if(self._double_Q==True):
            #next_q_vals_current_qnet=self.full_Q.predict(next_states_val+[np.zeros_like(Es)])
            next_q_vals_current_qnet=self.full_Q.predict(next_states_val)
            argmax_next_q_vals=np.argmax(next_q_vals_current_qnet, axis=1)
            max_next_q_vals=next_q_vals[np.arange(self._batch_size),argmax_next_q_vals].reshape((-1, 1))
        else:
            max_next_q_vals=np.max(next_q_vals, axis=1, keepdims=True)

        not_terminals=np.ones_like(terminals_val,dtype=float) - terminals_val
        
        target = rewards_val + not_terminals * self._df * max_next_q_vals.reshape((-1))
        
        #q_vals=self.full_Q.predict([states_val[0],np.zeros_like(Es)]) #np.zeros((self._batch_size,self.learn_and_plan.internal_dim))])
        q_vals=self.full_Q.predict([states_val[0]])
        
        # In order to obtain the individual losses, we predict the current Q_vals and calculate the diff
        q_val=q_vals[np.arange(self._batch_size), actions_val.reshape((-1,))]#.reshape((-1, 1))        
        diff = - q_val + target 
        loss_ind=pow(diff,2)
                
        q_vals[  np.arange(self._batch_size), actions_val.reshape((-1,))  ] = target
                
        # Is it possible to use something more flexible than this? 
        # Only some elements of next_q_vals are actual value that I target. 
        # My loss should only take these into account.
        # Workaround here is that many values are already "exact" in this update
        #if (self.update_counter<10000):
        noise_to_be_robust=np.zeros_like(Es) #np.random.normal(size=(self._batch_size,self.learn_and_plan.internal_dim))*0.#25

        loss=0
        #loss=self.full_Q.train_on_batch([states_val[0],noise_to_be_robust] , q_vals ) 
        loss=self.full_Q.train_on_batch(states_val , q_vals ) 
        #print "self.q_vals.optimizer.lr"
        #print K.eval(self.q_vals.optimizer.lr)
        self.loss_Q+=loss

        if(self.update_counter%100==0):
            print self.update_counter
        
        self.update_counter += 1        

        # loss*self._n_actions = np.average(loss_ind)
        return np.sqrt(loss),loss_ind


#    def train_model(self, states_val, actions_val, rewards_val, next_states_val, terminals_val):
#        """
#        Train the model based part
#
#        1. Set shared variable in states_shared, next_states_shared, actions_shared, rewards_shared, terminals_shared         
#        2. perform batch training
#
#        Parameters
#        -----------
#        states_val : list of batch_size * [list of max_num_elements* [list of k * [element 2D,1D or scalar]])
#        actions_val : b x 1 numpy array of integers
#        rewards_val : b x 1 numpy array
#        next_states_val : list of batch_size * [list of max_num_elements* [list of k * [element 2D,1D or scalar]])
#        terminals_val : b x 1 numpy boolean array
#
#        Returns
#        -------
#        Average loss of the batch training (RMSE)
#        Individual (square) losses for each tuple
#        """
#
#        onehot_actions = np.zeros((self._batch_size, self._n_actions))
#        onehot_actions[np.arange(self._batch_size), actions_val[:,0]] = 1
#        Es_=self.encoder.predict([next_states_val[0]])
#        Es=self.encoder.predict([states_val[0]])
#        ETs=self.transition.predict([Es,onehot_actions])
#
##        if(self.update_counter>3000):
#        self.loss_T2=self.transition2.train_on_batch([Es,onehot_actions], Es_)
##        if(self.update_counter%100==0):
##            loss=0.
##            for i in range (100):
##                loss+=self.transition2.train_on_batch([Es,onehot_actions], Es_)
##                if(i%10==0):
##                    print "loss/(i+1)"
##                    print loss/(i+1)
##            print "loss/100."
##            print loss/100.
#            #print K.get_value(self.transition2.optimizer.lr)
#            #print [ K.get_value(param)
#            #        for layer in self.encoder.layers
#            #        for param in layer.trainable_weights ][0][0]
#        return self.loss_T2



    def qValues(self, state_val):
        """ Get the q values for one belief state (without planning)

        Arguments
        ---------
        state_val : one belief state

        Returns
        -------
        The q values for the provided belief state
        """ 
        copy_state=copy.deepcopy(state_val) #Required because of the "hack" below
        #for i,o in enumerate(state):
        #    if(o.ndim==4): #FIXME
        #        copy_state[i]=copy_state[i][0,:,:,:]/128.-1

        #return self.full_Q.predict([np.expand_dims(state,axis=0) for state in state_val]+[np.zeros((self._batch_size,self.learn_and_plan.internal_dim))])[0]
        return self.full_Q.predict([np.expand_dims(state,axis=0) for state in copy_state])[0]

    def qValues_planning(self, state_val, R, gamma, T, Q, d=5):
        """ Get the q values for one belief state with a planning depth d

        Arguments
        ---------
        state_val : one belief state
        d : planning depth

        Returns
        -------
        The q values with planning depth d for the provided belief state
        """
        #print "state_val[0]"
        #print state_val[0]
        #print len(state_val)
#        print "state_val[0][0]"
#        print state_val[0][0]
#        print state_val[0].shape
        print "self.full_Q.predict(state_val)[0]"
        print self.full_Q.predict(state_val)[0]
        encoded_x = self.encoder.predict(state_val)
        ## DEBUG PURPOSES
#        print "encoded_x[0]"
#        print encoded_x[0]
        
        identity_matrix = np.diag(np.ones(self._n_actions))
        if(encoded_x.ndim==2):
            tile3_encoded_x=np.tile(encoded_x,(self._n_actions,1))
        elif(encoded_x.ndim==4):
            tile3_encoded_x=np.tile(encoded_x,(self._n_actions,1,1,1))
        else:
            print ("error")
        
        repeat_identity=np.repeat(identity_matrix,len(encoded_x),axis=0)
        ##print tile3_encoded_x
        ##print repeat_identity
        r_vals_d0=np.array(R.predict([tile3_encoded_x,repeat_identity]))
        #print "r_vals_d0"
        #print r_vals_d0
        r_vals_d0=r_vals_d0.flatten()
        print "r_vals_d0"
        print r_vals_d0
        next_x_predicted=T.predict([tile3_encoded_x,repeat_identity])
        #print "next_x_predicted"
        #print next_x_predicted
        one_hot_first_action=np.zeros((1,self._n_actions))
        one_hot_first_action[0]=1
        next_x_predicted=T.predict([next_x_predicted[0:1],one_hot_first_action])
        next_x_predicted=T.predict([next_x_predicted[0:1],one_hot_first_action])
        next_x_predicted=T.predict([next_x_predicted[0:1],one_hot_first_action])
        #print "next_x_predicted action 0 t4"
        #print next_x_predicted
        ## END DEBUG PURPOSES

        QD_plan=0
        for i in range(d+1): #TO DO: improve planning algorithm
            #print encoded_x
            Qd=self.qValues_planning_abstr(encoded_x, R, gamma, T, Q, d=i, branching_factor=[self._n_actions,2,2,2,2,2,2,2]).reshape(len(encoded_x),-1)
            print "Qd,i"
            print Qd,i
            QD_plan+=Qd
        QD_plan=QD_plan/(d+1)
        
        print "QD_plan"
        print QD_plan

        return QD_plan

#    def qValues_planning_abstr(self, state_abstr_val, R, gamma, T, Q, d, branching_factor=None):
#        """ 
#        """
#        branching_factor=self._n_actions #TO IMPROVE, use MCTS, etc...
#        n=len(state_abstr_val)
#        identity_matrix = np.diag(np.ones(self._n_actions))
#        
#        this_branching_factor=branching_factor
#                         
#        if (d==0):
#            return Q.predict([state_abstr_val]) # no change in the order of the actions
#        else:
#            # All actions are considered in the tree
#            repeat_identity=np.repeat(identity_matrix,len(state_abstr_val),axis=0) # no change in the order of the actions
#            if(state_abstr_val.ndim==2):
#                tile3_encoded_x=np.tile(state_abstr_val,(self._n_actions,1))
#            elif(state_abstr_val.ndim==4):
#                tile3_encoded_x=np.tile(state_abstr_val,(self._n_actions,1,1,1))
#            else:
#                print ("error")
#            
#            #print tile3_encoded_x
#            #print repeat_identity
#            r_vals_d0=np.array(R.predict([tile3_encoded_x,repeat_identity]))
#            #print "r_vals_d0"
#            #print r_vals_d0
#            r_vals_d0=r_vals_d0.flatten()
#            
#            gamma_vals_d0=np.array(gamma.predict([tile3_encoded_x,repeat_identity]))
#            #print "r_vals_d0"
#            #print r_vals_d0
#            gamma_vals_vals_d0=gamma_vals_d0.flatten()
#
#            next_x_predicted=T.predict([tile3_encoded_x,repeat_identity])
#            return r_vals_d0+gamma_vals_vals_d0*np.amax(self.qValues_planning_abstr(next_x_predicted,R,gamma,T,Q,d=d-1,branching_factor=branching_factor).reshape(len(state_abstr_val)*this_branching_factor,branching_factor),axis=1).flatten()
  
  
    def qValues_planning_abstr(self, state_abstr_val, R, gamma, T, Q, d, branching_factor=None):
        """ 
        """
        #if(branching_factor==None or branching_factor>self._n_actions):
        #    branching_factor=self._n_actions
        
        #print "qValues_planning_abstr d"
        #print d
        n=len(state_abstr_val)
        identity_matrix = np.diag(np.ones(self._n_actions))
        
        this_branching_factor=branching_factor.pop(0)
        if (n==1):
            # We require that the first branching factor is self._n_actions so that QD_plan has the right dimension
            this_branching_factor=self._n_actions
        #else:
        #    this_branching_factor=branching_factor
                         
        if (d==0):
            if(this_branching_factor<self._n_actions):
                return np.partition(Q.predict([state_abstr_val]), -this_branching_factor)[:,-this_branching_factor:]
            else:
                return Q.predict([state_abstr_val]) # no change in the order of the actions
        else:
            if(this_branching_factor==self._n_actions):
                # All actions are considered in the tree
                repeat_identity=np.repeat(identity_matrix,len(state_abstr_val),axis=0)
                if(state_abstr_val.ndim==2):
                    tile3_encoded_x=np.tile(state_abstr_val,(self._n_actions,1))
                elif(state_abstr_val.ndim==4):
                    tile3_encoded_x=np.tile(state_abstr_val,(self._n_actions,1,1,1))
                else:
                    print ("error")
            else:
                # A subset of the actions are considered in the tree
                estim_Q_values=Q.predict([state_abstr_val])
                #print estim_Q_values
                ind = np.argpartition(estim_Q_values, -this_branching_factor)[:,-this_branching_factor:]
                #print ind
                #print identity_matrix[ind]
                #repeat_identity=np.repeat(identity_matrix[ind],len(state_abstr_val),axis=0)
                repeat_identity=identity_matrix[ind].reshape(n*this_branching_factor,self._n_actions)
                #print repeat_identity
                #if(state_abstr_val.ndim==2):
                #    tile3_encoded_x=np.tile(state_abstr_val,(this_branching_factor,1))
                #elif(state_abstr_val.ndim==4):
                #    tile3_encoded_x=np.tile(state_abstr_val,(this_branching_factor,1,1,1))
                #else:
                #    print ("error")
                tile3_encoded_x=np.repeat(state_abstr_val,this_branching_factor,axis=0)
                #print "tile3_encoded_x"
                #print tile3_encoded_x
            
            #print tile3_encoded_x
            #print repeat_identity
            r_vals_d0=np.array(R.predict([tile3_encoded_x,repeat_identity]))
            #print "r_vals_d0"
            #print r_vals_d0
            r_vals_d0=r_vals_d0.flatten()
            
            gamma_vals_d0=np.array(gamma.predict([tile3_encoded_x,repeat_identity]))
            #print "r_vals_d0"
            #print r_vals_d0
            gamma_vals_d0=gamma_vals_d0.flatten()

            next_x_predicted=T.predict([tile3_encoded_x,repeat_identity])
            return r_vals_d0+gamma_vals_d0*np.amax(self.qValues_planning_abstr(next_x_predicted,R,gamma,T,Q,d=d-1,branching_factor=branching_factor).reshape(len(state_abstr_val)*this_branching_factor,branching_factor[0]),axis=1).flatten()

    def chooseBestAction(self, state, mode):
        """ Get the best action for a belief state

        Arguments
        ---------
        state : one belief state

        Returns
        -------
        The best action : int
        """
        copy_state=copy.deepcopy(state) #Required because of the "hack" below
        #for i,o in enumerate(state):
        #    if(o.ndim==4): #FIXME
        #        copy_state[i]=copy_state[i][0,:,:,:]/128.-1

        if(mode==None):
            mode=0
        di=[0,1,3,6]
        # We use the mode to define the planning depth
        q_vals = self.qValues_planning([np.expand_dims(s,axis=0) for s in copy_state],self.R,self.gamma, self.transition, self.Q, d=di[mode])

        return np.argmax(q_vals),np.max(q_vals)
        
    def _compile(self):
        """ compile self.q_vals
        """
        if (self._update_rule=="sgd"):
            optimizer = SGD(lr=self._lr, momentum=self._momentum, nesterov=False)
        elif (self._update_rule=="rmsprop"):
            optimizer = RMSprop(lr=self._lr, rho=self._rho, epsilon=self._rms_epsilon)
        else:
            raise Exception('The update_rule '+self._update_rule+' is not implemented.')
        
        self.full_Q.compile(optimizer=optimizer, loss='mse')

        optimizer1=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06) # Different optimizers for each network; otherwise not possible to modify each
        optimizer2=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06) # separately (e.g. lr)
        optimizer3=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        optimizer4=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        optimizer5=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        optimizer6=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        optimizer7=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        optimizer8=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)

        self.diff_Tx_x_.compile(optimizer=optimizer1, loss='mse') # Fit transitions
        #self.transition2.compile(optimizer=optimizer2, loss='mse') # Fit accurate transitions without encoders
        self.full_R.compile(optimizer=optimizer3, loss='mse') # Fit rewards
        self.full_gamma.compile(optimizer=optimizer3, loss='mse') # Fit discount

        if(self._high_int_dim==False):
            self.force_features.compile(optimizer=optimizer8,
                  loss=cosine_proximity2)

        self.encoder.compile(optimizer=optimizer4,
                  loss=mean_squared_error_p)
        self.encoder_diff.compile(optimizer=optimizer5,
                  loss=exp_dec_error)
                  #metrics=['accuracy'])

        self.diff_s_s_.compile(optimizer=optimizer6,
                  loss=exp_dec_error)#'mse')#loss_diff_s_s_)
                  #metrics=['accuracy'])

        self.diff_sa_sa.compile(optimizer=optimizer7,
                  loss=exp_dec_error)#loss_diff_s_s_)

#        self.diff_Tx.compile(optimizer=optimizer,
#                  loss=mean_squared_error)
#                  #metrics=['accuracy'])

    def _resetQHat(self):
        for i,(param,next_param) in enumerate(zip(self.params, self.params_target)):
            K.set_value(next_param,K.get_value(param))

    def setLearningRate(self, lr):
        """ Setting the learning rate

        Parameters
        -----------
        lr : float
            The learning rate that has to be set
        """
        self._lr = lr
        print "modif lr"
        # Changing the learning rates (NB:recompiling seems to lead to memory leaks!)
        K.set_value(self.full_Q.optimizer.lr, self._lr)

        K.set_value(self.full_R.optimizer.lr, self._lr)
        K.set_value(self.full_gamma.optimizer.lr, self._lr)
        K.set_value(self.diff_Tx_x_.optimizer.lr, self._lr)
        
        if(self._high_int_dim==False):
            K.set_value(self.force_features.optimizer.lr, 0)#self._lr)

        K.set_value(self.encoder.optimizer.lr, self._lr)
        K.set_value(self.encoder_diff.optimizer.lr, self._lr)

        K.set_value(self.diff_s_s_.optimizer.lr, self._lr/5.) # /5. for simple laby or simple catcher; /1 for distrib of laby
        K.set_value(self.diff_sa_sa.optimizer.lr, 0) # 0 !
#        K.set_value(self.diff_Tx.optimizer.lr, self._lr/10.)

    def transfer(self, original, transfer, epochs=1):
        # First, make sure that the target network and the current network are the same
        self._resetQHat()
        # modify the loss of the encoder
        #self.encoder=self.learn_and_plan.encoder_model()
        #for l in self.encoder.layers[-5:]:
        #    l.trainable = False # Freeze dense layers # DOES NOT SEEM TO HELP (transfer on catcher)
        #print "self.encoder.layers[-1].get_weights()"
        #print self.encoder.layers[-1].get_weights()
        
        optimizer4=RMSprop(lr=self._lr, rho=0.9, epsilon=1e-06)
        self.encoder.compile(optimizer=optimizer4,
                  loss='mse')
        
        # Then, train the encoder such that the original and transfer states are mapped into the same abstract representation
        x_original=self.encoder.predict(original)#[0]
        print "x_original[0:10]"
        print x_original[0:10]
        for i in range(epochs):
            size = original[0].shape[0]
            #print size
            #print transfer[0][0:int(size*0.8)] , x_original[0:int(size*0.8)]
            print "train"
            print self.encoder.train_on_batch(transfer[0][0:int(size*0.8)] , x_original[0:int(size*0.8)] )
            #print self.encoder.train_on_batch(original[0][0:int(size*0.8)] , x_original[0:int(size*0.8)] )
            print "validation"
            print self.encoder.test_on_batch(transfer[0][int(size*0.8):] , x_original[int(size*0.8):])
            #print self.encoder.test_on_batch(original[0][int(size*0.8):] , x_original[int(size*0.8):] )
         
        self.encoder.compile(optimizer=optimizer4,
                  loss=mean_squared_error_p)
